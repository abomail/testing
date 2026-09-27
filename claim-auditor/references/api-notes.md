# API Notes (all free, keyless by default)

| Registry | Endpoint | Used for | Limits / notes |
|---|---|---|---|
| Crossref | `GET https://api.crossref.org/works/{doi}` | DOI identity, metadata, retraction subject terms | Polite pool: send real `User-Agent`. ~50k/day fine. Add `?mailto=you@x.org` to join polite pool. |
| OpenAlex | `GET https://api.openalex.org/works/doi:{doi}` and `?filter=title_and_abstract.search:` | Abstract reconstruction (inverted index), `is_retracted` flag | 10 req/s, 100k/day free. Send `From:` header. |
| PubMed E-utilities | `esummary.fcgi` + `efetch.fcgi` | PMID identity, pubtypes ("Published Retraction Notice") | 3 req/s without key; free API key raises to 10/s. |
| arXiv | `GET http://export.arxiv.org/api/query?id_list=` | Preprint identity + abstract | Be gentle (~1 req/3s). Preprints flagged AMBER. |

Optional environment variables (never required):
- `CLAIM_AUDITOR_MAILTO` — replaces the default `From:` mailto for polite pools.
- `NCBI_API_KEY` — append `&api_key=` automatically if present (future versions).

Failure policy: any HTTP error ⇒ treat as *miss*, try fallback registry; all
misses ⇒ `UNRESOLVED_IDENTITY` (RED). The tool never fabricates metadata.

Retrieval completeness caveat: abstracts are absent in some Crossref records;
OpenAlex reconstructs most. If no abstract exists anywhere, the claim can only
ever be graded `INSUFFICIENT_EVIDENCE` — say so, do not judge from titles alone.
