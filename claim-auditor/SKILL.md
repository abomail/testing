---
name: claim-auditor
description: >-
  Audit citation-to-claim alignment in academic manuscripts. Verifies that each
  reference actually supports the sentence that cites it, detects retracted or
  expressions-of-concern papers, flags self-citation padding and overclaiming,
  and produces a deterministic, evidence-graded audit report (JSON + Markdown).
  Use whenever the user asks to check citations, verify references, audit claims,
  validate a literature review, or prepare a manuscript before submission.
license: MIT
compatibility: >-
  Model-agnostic and harness-agnostic. Requires only Python 3.9+ (stdlib only)
  and network access for bibliographic APIs (Crossref, OpenAlex, Semantic
  Scholar, PubMed). Works with Claude Code, OpenAI Codex CLI, opencode, Cursor,
  Windsurf, Aider, Goose, Amp, Cline, and any agent that can read a SKILL.md
  and execute shell commands. No MCP server required.
metadata:
  version: "1.0.0"
  author: claim-auditor
  category: research-integrity
  tags: [citations, claims, research-integrity, peer-review, students, researchers]
---

# Claim Auditor

Verify that every citation in a manuscript **actually supports the claim made
in the sentence that cites it** — before reviewers, editors, or retractions do.

## Why this exists

LLM-assisted writing has flooded submissions with *orphaned citations*:
references that are real papers but do not support the sentence they are
attached to. Manual checking is slow; pure-LLM checking hallucinates. This
skill splits the work:

- **Deterministic tools** (Python stdlib CLI) do everything provable: parse the
  document, resolve each DOI/arXiv/PubMed ID against live registries, detect
  retractions, compute statistics, and emit a machine-readable ledger.
- **The agent model you already run** does the one judgment task: compare the
  citing sentence against the paper's abstract/titles and grade alignment
  (SUPPORTED / PARTIAL / MISMATCH / INSUFFICIENT_EVIDENCE).

No vendor lock-in, no API keys required (optional keys raise rate limits),
no install step beyond `python3`.

## Inputs supported

| Input | How |
|---|---|
| LaTeX `.tex` | `\cite{...}`, `\citep`, `\citet`, biblatex natbib |
| Markdown | `[1]`, `[@key]` (Pandoc), `<sup>1</sup>` |
| Plain text / pasted draft | numbered `[12]` markers |
| `.docx` | auto-extract via `extract-text` subcommand (zipfile, stdlib) |
| Bibliography | `.bib` (BibTeX), or inline `thebibliography` |

## Workflow (follow these steps exactly)

### Step 1 — Extract & resolve (deterministic)

```bash
python3 scripts/audit.py extract --input <manuscript> --bib <refs.bib> --out work/claims.json
python3 scripts/audit.py resolve --work work/claims.json          # Crossref/OpenAlex/PubMed/arXiv
python3 scripts/audit.py integrity --work work/claims.json        # retraction / EoC checks
```

`resolve` attaches to every cited entry: DOI, title, journal, year, abstract
(when open), and identity status. Entries that cannot be resolved against any
registry are marked `UNRESOLVED_IDENTITY` — treat them as fabricated until
proven real.

### Step 2 — Alignment judgment (you, the agent)

Read `work/claims.json`. For every claim whose `resolution.status == "resolved"`:

1. Read the `sentence` field (the citing sentence, markers stripped).
2. Read the referenced paper's `title` + `abstract` from `resolution`.
3. Grade alignment using **only** what the fetched abstract states — never your
   parametric memory about the paper:
   - `SUPPORTED` — abstract directly states the claim (or its exact contrapositive).
   - `PARTIAL` — related topic, but the specific claim is broader/narrower/different population.
   - `MISMATCH` — abstract contradicts or is unrelated to the claim.
   - `INSUFFICIENT_EVIDENCE` — abstract silent on the claim.
4. Write verdicts to `work/verdicts.json` (schema: see `references/schemas.md`).
   Each verdict MUST include a one-sentence `justification` quoting ≤25 words
   from the abstract. If you cannot quote support, the verdict is not SUPPORTED.

Batch rule: judge at most 20 claims per pass; if more remain, loop Step 2.

### Step 3 — Report (deterministic merge)

```bash
python3 scripts/audit.py report --work work/claims.json --verdicts work/verdicts.json \
    --out audit-report/
```

Produces `audit-report/report.md` (human) and `audit-report/results.json`
(machine), including:

- Per-claim table with grades, justifications, and DOIs.
- Retraction alerts (RED banner if any cited paper is retracted).
- Overclaim lexicon hits ("proves", "causes", "guaranteed") vs. correlational abstracts.
- Self-citation ratio, repeated-cluster density, orphan references (in bib but
  never cited) and ghost citations (cited key missing from bib).
- A **submission readiness score** 0–100 with the exact blocking issues listed.

### Step 4 — Present to the user

Summarize: total claims audited, count per grade, every RED flag verbatim,
and the top 5 fixes ranked by risk. Never soften a MISMATCH or retraction
finding into hedged language.

## Hard rules (guardrails)

1. **Never assert a paper says something without quoting its fetched abstract.**
   Parametric-memory "I know this paper" is forbidden evidence.
2. **Unresolvable DOI/key ⇒ report as unverifiable**, do not guess a match.
3. **Retraction status comes from the registry response**, not the model.
4. Do not edit the manuscript automatically; propose fixes as diffs only when
   asked.
5. Respect paywalls: use publicly available metadata APIs only.

## Resources

- `scripts/audit.py` — single-file stdlib CLI (extract/resolve/integrity/report/extract-text).
- `references/schemas.md` — JSON contracts for claims.json / verdicts.json.
- `references/api-notes.md` — endpoint details, polite-pool headers, optional keys.
- `examples/demo/` — a tiny LaTeX manuscript + .bib with planted defects to test-run the pipeline.
