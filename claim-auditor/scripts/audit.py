#!/usr/bin/env python3
"""claim-auditor: deterministic citation-identity + integrity engine.

Stdlib-only (Python 3.9+). Subcommands:
  extract       parse manuscript + bib -> work/claims.json
  resolve       verify each cited reference against live registries
  integrity     retraction / expression-of-concern checks
  report        merge agent verdicts -> Markdown + JSON audit report
  extract-text  dump text from .docx (stdlib zipfile)

Design contract: this tool NEVER judges claim alignment. It resolves identity,
fetches abstracts, flags registry-level integrity problems, and computes
statistics. The agent model grades alignment in verdicts.json.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser

VERSION = "1.0.0"
UA = f"claim-auditor/{VERSION} (research-integrity tool; https://skills.sh)"

# ---------------------------------------------------------------- utilities

def http_get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # network down, DNS, TLS...
        return 0, str(e)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"[ok] wrote {path}")


def strip_markers(text):
    text = re.sub(r"\\citep?\[.*?\]\{[^}]*\}", "", text)
    text = re.sub(r"\\citet?\{[^}]*\}", "", text)
    text = re.sub(r"\[\d+(?:[,–\-]\s*\d+)*\]", "", text)
    text = re.sub(r"\[@?[A-Za-z0-9_:.\-]+\]", "", text)
    text = re.sub(r"<sup>.*?</sup>", "", text)
    return re.sub(r"\s+", " ", text).strip(" ,;.")


# ---------------------------------------------------------------- BibTeX

ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,]+),\s*(.*?)\n\s*\}", re.S)
FIELD_RE = re.compile(r"(\w+)\s*=\s*(?:\{|\")(.*?)(?:\}|\")\s*,?\s*", re.S)


def parse_bib(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    raw = re.sub(r"@[Cc][Oo][Mm][Mm][Ee][Nn][Tt].*?\n\n", "\n", raw)
    entries = {}
    for m in ENTRY_RE.finditer(raw):
        etype, key, body = m.group(1).lower(), m.group(2).strip(), m.group(3)
        fields = {}
        for fm in FIELD_RE.finditer(body):
            fields[fm.group(1).lower()] = re.sub(r"\s+", " ", fm.group(2)).strip("{}")
        entries[key] = {"type": etype, "fields": fields}
    return entries


def bib_doi(fields):
    for k in ("doi", "electronicoius"):
        if k in fields:
            return fields[k].lower().replace("https://doi.org/", "").strip()
    return None


# ---------------------------------------------------------------- LaTeX inline bibliography

def parse_thebibliography(tex):
    """Parse \begin{thebibliography} ... \bibitem{key} ref-text."""
    out = {}
    block = re.search(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", tex, re.S)
    if not block:
        return out
    for m in re.finditer(r"\\bibitem(?:\[[^\]]*\])?\s*\{([^}]+)\}\s*(.*?)(?=\\bibitem|\\end)", block.group(0), re.S):
        out[m.group(1)] = {"type": "misc", "fields": {"note": re.sub(r"\s+", " ", m.group(2)).strip()}}
    return out


# ---------------------------------------------------------------- citation extraction

CITE_RE = re.compile(r"\\(?P<cmd>citep|citet|cite|autocite|parencite|textcite)\*?(?:\[(?P<pre>[^\]]*)\])?\{(?P<keys>[^}]*)\}")
MD_NUM_RE = re.compile(r"(?<!\S)\[(?P<n>\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\](?!\[)")
MD_PANDOC_RE = re.compile(r"\[(?P<body>(?:[^][]|\\.)*?(?:;\s*@)[^][]*?)\]")
SUP_RE = re.compile(r"<sup>(?P<n>[\d,\s–-]+)</sup>")


def sentences_with_spans(text):
    """Crude sentence splitter that keeps byte spans so we can locate cites."""
    spans = []
    start = 0
    for i in range(len(text) - 1):
        if text[i] in ".!?" and text[i + 1] in " \n\t" and not re.search(r"(?:[A-Z]\.|et al\.|etc\.|Fig\.|Ref\.)$", text[start:i + 1]):
            spans.append((start, i + 1))
            start = i + 1
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def find_claims(tex_or_md, is_latex):
    claims = []
    text = tex_or_md
    if is_latex:
        # remove comments & preamble/structure noise, but keep cite commands intact
        text = re.sub(r"(?<!\\)%.*", "", text)
        text = re.sub(r"\\(documentclass|usepackage|maketitle|tableofcontents)\b[^{]*", " ", text)
        text = re.sub(r"\\(?:title|author|date|section|subsection|subsubsection|paragraph|caption)\*?(?:\[[^\]]*\])?\{([^}]*)\}", r" \1 ", text)
        text = re.sub(r"\\(?:begin|end)\{[^}]*\}", " ", text)
        # residual commands: strip only those NOT in the cite family (protects \cite[..]{..})
        text = re.sub(r"\\(?!cite|autocite|parencite|textcite|nocite)[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", text)
        text = re.sub(r"\n\s*\n", "\n", text)
    for s, e in sentences_with_spans(text):
        sent_raw = text[s:e].strip()
        if not sent_raw:
            continue
        found = []
        if is_latex:
            for cm in CITE_RE.finditer(sent_raw):
                keys = [k.strip() for k in cm.group("keys").split(",") if k.strip()]
                found.append(("latex", keys))
        else:
            for mm in MD_PANDOC_RE.finditer(sent_raw):
                body = mm.group("body")
                if "@" in body:
                    keys = re.findall(r"@([A-Za-z0-9_:\-.]+)", body)
                    if keys:
                        found.append(("pandoc", keys))
            for nm in MD_NUM_RE.finditer(sent_raw):
                nums = [int(x) for x in re.findall(r"\d+", nm.group("n"))]
                if nums:
                    found.append(("numbered", nums))
            for sm in SUP_RE.finditer(sent_raw):
                nums = [int(x) for x in re.findall(r"\d+", sm.group("n"))]
                if nums:
                    found.append(("numbered", nums))
        for style, refs in found:
            claims.append({
                "id": f"C{len(claims)+1:04d}",
                "style": style,
                "refs": refs,
                "sentence": clean_display(sent_raw, is_latex)[:600],
                "raw": sent_raw[:600],
            })
    return claims


def clean_display(sent_raw, is_latex):
    """Human-readable sentence: markers stripped, LaTeX noise removed."""
    out = strip_markers(sent_raw)
    if is_latex:
        out = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^}]*)\}", r"\1", out)  # e.g. {Introduction}
        out = re.sub(r"(?<!\\)[{}]", " ", out)
        out = re.sub(r"\\[a-zA-Z]+\*?", " ", out)
    return re.sub(r"\s+", " ", out).strip()


# ---------------------------------------------------------------- registry resolution

RETRACT_WORDS = re.compile(r"retract|expression of concern", re.I)


def resolve_crossref(doi):
    status, body = http_get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
    if status != 200:
        return None
    try:
        j = json.loads(body)["message"]
    except Exception:
        return None
    titles = j.get("title") or [""]
    container = (j.get("container-title") or [""])
    year = None
    for k in ("published-print", "published-online", "issued"):
        if j.get(k, {}).get("date-parts"):
            year = j[k]["date-parts"][0][0]
            break
    rel = " ".join(j.get("relation", {}).get("is-referenced-by-count", []) or []) if False else ""
    subject = " ".join(j.get("subject", []))
    link_body = json.dumps(j).lower()
    flagged = bool(RETRACT_WORDS.search(subject)) or "retracted" in str(j.get("status", "")).lower()
    return {
        "source": "crossref",
        "doi": doi,
        "title": titles[0],
        "journal": container[0],
        "year": year,
        "abstract": j.get("abstract", ""),
        "flagged_retraction_metadata": flagged,
        "link_relation_note": rel,
    }


def resolve_openalex(doi=None, title=None):
    if doi:
        url = f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi)}"
    elif title:
        # exact-ish title search; filter guarantees relevance better than plain ?search=
        url = ("https://api.openalex.org/works?filter=title.search:"
               + urllib.parse.quote(title[:200]) + "&per-page=5")
    else:
        return None
    mailto = os.environ.get("CLAIM_AUDITOR_MAILTO", "claim-auditor@skills.sh")
    status, body = http_get(url, headers={"From": f"mailto:{mailto}"})
    if status != 200:
        return None
    try:
        j = json.loads(body)
        cands = j.get("results") or [j]
    except Exception:
        return None
    w = cands[0]
    if title and len(cands) > 1:
        # pick candidate with highest token overlap with the requested title
        want = set(re.findall(r"[a-z0-9]+", title.lower()))
        def score(c):
            have = set(re.findall(r"[a-z0-9]+", (c.get("title") or "").lower()))
            return len(want & have) / max(1, len(want))
        best = max(cands, key=score)
        if score(best) >= 0.6:
            w = best
    inv = w.get("abstract_inverted_index")
    abstract = ""
    if inv:
        pos = {}
        for word, idxs in inv.items():
            for i in idxs:
                pos[i] = word
        abstract = " ".join(pos[k] for k in sorted(pos))
    oa_type = w.get("type", "")
    ids = w.get("ids", {}) or {}
    return {
        "source": "openalex",
        "doi": (ids.get("doi") or "").replace("https://doi.org/", "") or doi,
        "title": w.get("title") or (title or ""),
        "journal": (w.get("primary_location") or {}).get("source", {}).get("display_name") if (w.get("primary_location") or {}).get("source") else "",
        "year": w.get("publication_year"),
        "abstract": abstract,
        "openalex_type": oa_type,
        "retracted_flag": bool(w.get("is_retracted")),
        "eoc_flag": bool(w.get("is_paratext")),
    }


def resolve_pubmed(pid):
    status, body = http_get(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={pid}&retmode=json")
    if status != 200:
        return None
    try:
        j = json.loads(body)["result"][pid]
    except Exception:
        return None
    abstract = ""
    s2, vbody = http_get(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pid}&rettype=abstract&retmode=text")
    if s2 == 200:
        abstract = vbody[:4000]
    pubtypes = " ".join(pt.get("value", "") for pt in j.get("pubtypes", []) or [])
    return {
        "source": "pubmed",
        "pmid": pid,
        "doi": next((a.get("value") for a in j.get("articleids", []) if a.get("idtype") == "doi"), None),
        "title": j.get("title", ""),
        "journal": j.get("fulljournalname", ""),
        "year": (j.get("epubdate", "") or j.get("pubdate", ""))[:4] or None,
        "abstract": abstract,
        "pubtypes": pubtypes,
        "flagged_retraction_metadata": bool(RETRACT_WORDS.search(pubtypes)),
    }


def resolve_arxiv(aid):
    aid = aid.split("v")[0] if re.match(r"^\d{4}\.\d{4,5}", aid) else aid
    status, body = http_get(f"http://export.arxiv.org/api/query?id_list={urllib.parse.quote(aid)}")
    if status != 200 or "<entry>" not in body:
        return None
    entry = body.split("<entry>")[1]
    def tag(t):
        m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", entry, re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    return {
        "source": "arxiv",
        "arxiv_id": aid,
        "title": tag("title"),
        "journal": "arXiv preprint",
        "year": (tag("published") or "")[:4] or None,
        "abstract": tag("summary"),
        "is_preprint": True,
    }


def guess_identifier(ref_str):
    """Pull DOI / arXiv / PMID out of a free-form reference string."""
    doi = None
    m = re.search(r"10\.\d{4,9}/[^\s\"'}<>]+", ref_str)
    if m:
        doi = m.group(0).rstrip(".,;)")
    mx = re.search(r"arXiv\s*:?\s*(\d{4}\.\d{4,5}(?:v\d+)?)", ref_str, re.I)
    pm = re.search(r"(?:PMID|PubMed):?\s*(\d{6,8})", ref_str, re.I)
    return doi, (mx.group(1) if mx else None), (pm.group(1) if pm else None)


def resolve_one(entry):
    """entry: dict with 'bib' fields (optional) and 'ref_strings'. Returns resolution dict."""
    fields = entry.get("bib_fields") or {}
    note = fields.get("note", "") + " " + fields.get("howpublished", "") + " " + json.dumps(fields)
    doi = bib_doi(fields)
    arx = None
    pmid = None
    if not doi:
        doi, arx, pmid = guess_identifier(note)
    if not doi and fields.get("eprint"):
        arx = fields["eprint"]
    if not doi and fields.get("pmid"):
        pmid = fields["pmid"]
    title = fields.get("title") or ""

    attempts = []
    rec = None
    if doi:
        rec = resolve_crossref(doi)
        attempts.append(f"crossref(doi={doi}):{'hit' if rec else 'miss'}")
        if not rec or not rec.get("abstract"):
            oa = resolve_openalex(doi=doi)
            attempts.append(f"openalex(doi):{'hit' if oa else 'miss'}")
            if oa:
                rec = {**(rec or {}), **{k: v for k, v in oa.items() if not rec or not rec.get(k)}, "source": (rec or {}).get('source', 'openalex') + "+" + oa['source']}
    elif arx:
        rec = resolve_arxiv(arx)
        attempts.append(f"arxiv({arx}):{'hit' if rec else 'miss'}")
    elif pmid:
        rec = resolve_pubmed(pmid)
        attempts.append(f"pubmed({pmid}):{'hit' if rec else 'miss'}")

    if not rec and title:
        oa = resolve_openalex(title=title)
        attempts.append(f"openalex(title):{'hit' if oa and oa.get('title') else 'miss'}")
        if oa and oa.get("title"):
            rec = oa

    if rec and (rec.get("title") or rec.get("abstract")):
        rec["status"] = "resolved"
        # title-match guard: when matched by title search, verify overlap
        if title and rec.get("source", "").startswith("openalex") and not doi and not arx and not pmid:
            want = set(re.findall(r"[a-z0-9]+", title.lower()))
            have = set(re.findall(r"[a-z0-9]+", (rec.get("title") or "").lower()))
            ov = len(want & have) / max(1, len(want))
            rec["title_match_overlap"] = round(ov, 2)
            if ov < 0.6:
                rec = {"status": "UNRESOLVED_IDENTITY",
                       "near_match_title": rec.get("title"), "overlap": ov}
    else:
        rec = {"status": "UNRESOLVED_IDENTITY"}
    rec["attempts"] = attempts
    return rec


# ---------------------------------------------------------------- commands

def cmd_extract(args):
    raw = open(args.input, encoding="utf-8", errors="replace").read()
    is_latex = args.input.endswith(".tex") or "\\cite" in raw or "\\documentclass" in raw
    claims = find_claims(raw, is_latex)

    bib = {}
    if args.bib and os.path.exists(args.bib):
        bib = parse_bib(args.bib)
    if is_latex:
        bib.update(parse_thebibliography(raw))

    # numbered style: build positional map from bib order or thebibliography order
    ordered_keys = None
    if any(c["style"] == "numbered" for c in claims):
        if is_latex and "\\bibitem" in raw:
            ordered_keys = re.findall(r"\\bibitem(?:\[[^\]]*\])?\s*\{([^}]+)\}", raw)
        elif bib:
            ordered_keys = list(bib.keys())

    for c in claims:
        resolved_refs = []
        for r in c["refs"]:
            if isinstance(r, int) and ordered_keys:
                key = ordered_keys[r - 1] if 1 <= r <= len(ordered_keys) else f"__missing_number_{r}"
                resolved_refs.append({"marker": r, "key": key})
            else:
                resolved_refs.append({"marker": r, "key": r})
        c["targets"] = resolved_refs
        for t in resolved_refs:
            e = bib.get(t["key"])
            t["in_bib"] = e is not None
            t["bib_fields"] = (e or {}).get("fields", {})
            t["bib_type"] = (e or {}).get("type")

    cited_keys = {t["key"] for c in claims for t in c["targets"]}
    orphans = sorted(set(bib) - cited_keys)
    ghosts = sorted({t["key"] for c in claims for t in c["targets"] if not t["in_bib"] and not str(t["key"]).startswith("__missing")})

    doc = {
        "meta": {"tool_version": VERSION, "input": args.input, "bib": args.bib,
                 "parser": "latex" if is_latex else "markdown/text",
                 "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        "stats": {"claims": len(claims), "unique_keys_cited": len(cited_keys),
                  "bib_entries": len(bib), "orphans": orphans, "ghosts": ghosts},
        "claims": claims,
    }
    save_json(args.out, doc)
    print(f"[extract] {len(claims)} claims | {len(bib)} bib entries | "
          f"{len(orphans)} orphan refs | {len(ghosts)} ghost citations")
    if ghosts:
        print("[warn] cited keys missing from bibliography:", ", ".join(ghosts[:10]))


def cmd_resolve(args):
    doc = load_json(args.work)
    seen = {}
    for c in doc["claims"]:
        for t in c["targets"]:
            key = t["key"]
            if key not in seen:
                print(f"[resolve] {key} ...", flush=True)
                ident = {"bib_fields": t.get("bib_fields") or {}}
                if not ident["bib_fields"] and t.get("ref_text"):
                    ident["bib_fields"] = {"note": t["ref_text"]}
                seen[key] = resolve_one(ident)
                time.sleep(args.delay)
            t["resolution"] = seen[key]
    unresolved = sum(1 for v in seen.values() if v.get("status") != "resolved")
    doc["stats"]["unresolved_identity"] = unresolved
    save_json(args.work, doc)
    print(f"[resolve] done. {len(seen)} unique refs, {unresolved} UNRESOLVED_IDENTITY")


def cmd_integrity(args):
    doc = load_json(args.work)
    alerts = []
    for c in doc["claims"]:
        for t in c["targets"]:
            r = t.get("resolution") or {}
            key = t["key"]
            if r.get("retracted_flag") or r.get("flagged_retraction_metadata"):
                alerts.append({"level": "RED", "type": "RETRACTION", "key": key,
                               "detail": f"'{r.get('title','')}' carries retraction metadata in registry."})
            if r.get("eoc_flag"):
                alerts.append({"level": "RED", "type": "EXPRESSION_OF_CONCERN", "key": key,
                               "detail": r.get("title", key)})
            if r.get("status") == "UNRESOLVED_IDENTITY":
                alerts.append({"level": "RED", "type": "UNVERIFIABLE_REFERENCE", "key": key,
                               "detail": "No registry match for DOI/title/id. Possible fabrication."})
            if r.get("is_preprint"):
                alerts.append({"level": "AMBER", "type": "PREPRINT_CITED", "key": key,
                               "detail": f"Not peer-reviewed: {r.get('title','')}"})
            pubtypes = r.get("pubtypes", "")
            if RETRACT_WORDS.search(pubtypes):
                alerts.append({"level": "RED", "type": "RETRACTION", "key": key,
                               "detail": f"PubMed pubtypes: {pubtypes}"})
    doc.setdefault("integrity", {})["alerts"] = alerts
    save_json(args.work, doc)
    reds = [a for a in alerts if a["level"] == "RED"]
    print(f"[integrity] {len(alerts)} alerts ({len(reds)} RED)")


OVERCLAIM_TERMS = re.compile(r"\b(proves?|guarantee[sd]?|definitive(?:ly)?|always causes?|"
                             r"never fails?|all studies show|every researcher|"
                             r"conclusively?|undeniable|proven\b)", re.I)
CORRELATIONAL = re.compile(r"\b(correlat|associat|observ|linked|may |might|suggests?|"
                           r"associated with|no significant|failed to replicate)\b", re.I)


def cmd_report(args):
    doc = load_json(args.work)
    verdicts = {}
    if args.verdicts and os.path.exists(args.verdicts):
        for v in load_json(args.verdicts).get("verdicts", []):
            verdicts[v["claim_id"]] = v

    rows = []
    grade_count = {"SUPPORTED": 0, "PARTIAL": 0, "MISMATCH": 0,
                   "INSUFFICIENT_EVIDENCE": 0, "NOT_JUDGED": 0}
    for c in doc["claims"]:
        v = verdicts.get(c["id"])
        grade = v["grade"] if v else "NOT_JUDGED"
        grade_count[grade] = grade_count.get(grade, 0) + 1
        oc = OVERCLAIM_TERMS.findall(c["sentence"])
        corr = bool(CORRELATIONAL.search(" ".join((t.get("resolution") or {}).get("abstract", "")
                                                  for t in c["targets"])))
        overclaim = bool(oc) and corr and grade in ("MISMATCH", "PARTIAL", "INSUFFICIENT_EVIDENCE")
        rows.append({
            "id": c["id"], "sentence": c["sentence"],
            "refs": [t["key"] for t in c["targets"]],
            "dois": [(t.get("resolution") or {}).get("doi") for t in c["targets"]],
            "grade": grade,
            "justification": v.get("justification", "") if v else "",
            "overclaim_flag": overclaim,
            "overclaim_terms": oc,
        })

    alerts = (doc.get("integrity") or {}).get("alerts", [])
    reds = [a for a in alerts if a["level"] == "RED"]
    ambers = [a for a in alerts if a["level"] == "AMBER"]
    st = doc["stats"]
    judged = [r for r in rows if r["grade"] != "NOT_JUDGED"]

    score = 100
    blockers = []
    if reds:
        score -= min(40, 12 * len(reds)); blockers.append(f"{len(reds)} RED integrity alert(s)")
    if st.get("ghosts"):
        score -= min(15, 5 * len(st["ghosts"])); blockers.append(f"{len(st['ghosts'])} ghost citation(s)")
    if st.get("orphans"):
        score -= min(10, 2 * len(st["orphans"])); blockers.append(f"{len(st['orphans'])} orphan reference(s)")
    mism = grade_count.get("MISMATCH", 0)
    if mism:
        score -= min(30, 10 * mism); blockers.append(f"{mism} MISMATCH claim(s)")
    if grade_count.get("NOT_JUDGED", 0):
        score -= min(15, grade_count["NOT_JUDGED"]); blockers.append("claims left unjudged")
    occ = sum(1 for r in rows if r["overclaim_flag"])
    if occ:
        score -= min(10, 2 * occ); blockers.append(f"{occ} overclaim flag(s)")
    score = max(0, score)

    ready = score >= 85 and not reds and not mism

    outdir = args.out.rstrip("/")
    os.makedirs(outdir, exist_ok=True)
    results = {"score": score, "submission_ready": ready, "blockers": blockers,
               "grades": grade_count, "red_alerts": reds, "amber_alerts": ambers,
               "rows": rows, "stats": st, "meta": doc["meta"]}
    save_json(os.path.join(outdir, "results.json"), results)

    md = [f"# Claim Audit Report", "",
          f"- File: `{doc['meta']['input']}`  ·  Tool v{VERSION}  ·  {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
          f"- Claims: **{st['claims']}** · Grades: " + " / ".join(f"{k}:{v}" for k, v in grade_count.items()),
          f"- **Submission readiness score: {score}/100 — {'READY' if ready else 'NOT READY'}**", ""]
    if reds:
        md += ["> ## 🛑 RED FLAGS (blocking)", ""]
        md += [f"> - **{a['type']}** `{a['key']}` — {a['detail']}" for a in reds]
        md += [""]
    if ambers:
        md += ["### Amber advisories", ""]
        md += [f"- **{a['type']}** `{a['key']}` — {a['detail']}" for a in ambers]
        md += [""]
    md += ["## Per-claim findings", "",
           "| ID | Grade | Claim (truncated) | Refs | Justification / issue |",
           "|----|-------|-------------------|------|--------------------------|"]
    icon = {"SUPPORTED": "✅", "PARTIAL": "🟠", "MISMATCH": "❌",
            "INSUFFICIENT_EVIDENCE": "❔", "NOT_JUDGED": "⬜"}
    for r in rows:
        sent = r["sentence"][:80].replace("|", "/")
        just = (r["justification"] or ("overclaim terms: " + ",".join(r["overclaim_terms"]) if r["overclaim_flag"] else "")).replace("|", "/")[:90]
        md.append(f"| {r['id']} | {icon.get(r['grade'],'')} {r['grade']} | {sent}… | {', '.join(r['refs'])[:60]} | {just} |")
    md += ["", "## Blockers to fix before submission", ""]
    md += [f"1. {b}" for b in blockers] or ["None. "]
    md += ["", "## Hygiene", "",
           f"- Orphan references (uncited): {', '.join(st.get('orphans', [])) or 'none'}",
           f"- Ghost citations (not in bib): {', '.join(st.get('ghosts', [])) or 'none'}"]
    path = os.path.join(outdir, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"[report] {path} | score={score}/100 ready={ready} blockers={len(blockers)}")


def cmd_extract_text(args):
    with zipfile.ZipFile(args.input) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    text = re.sub(r"<w:p[ >]", "\n<w:p ", xml)
    text = re.sub(r"<[^>]+>", "", text)
    out = args.out or os.path.splitext(args.input)[0] + ".txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[extract-text] {out} ({len(text)} chars)")


# ---------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(prog="audit.py", description="claim-auditor deterministic engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract"); e.add_argument("--input", required=True)
    e.add_argument("--bib"); e.add_argument("--out", default="work/claims.json")
    e.set_defaults(fn=cmd_extract)

    r = sub.add_parser("resolve"); r.add_argument("--work", required=True)
    r.add_argument("--delay", type=float, default=0.6)
    r.set_defaults(fn=cmd_resolve)

    i = sub.add_parser("integrity"); i.add_argument("--work", required=True)
    i.set_defaults(fn=cmd_integrity)

    o = sub.add_parser("report"); o.add_argument("--work", required=True)
    o.add_argument("--verdicts"); o.add_argument("--out", default="audit-report")
    o.set_defaults(fn=cmd_report)

    x = sub.add_parser("extract-text"); x.add_argument("--input", required=True)
    x.add_argument("--out"); x.set_defaults(fn=cmd_extract_text)

    args = p.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
