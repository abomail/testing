# JSON Contracts

## work/claims.json (produced by `extract`, enriched by `resolve` + `integrity`)

```json
{
  "meta": {"tool_version": "1.0.0", "input": "paper.tex", "bib": "refs.bib",
           "parser": "latex", "generated_at": "..."},
  "stats": {"claims": 12, "unique_keys_cited": 9, "bib_entries": 10,
            "orphans": ["unused_key"], "ghosts": ["fakeKey2024"],
            "unresolved_identity": 1},
  "claims": [
    {
      "id": "C0001",
      "style": "latex | pandoc | numbered",
      "refs": ["smith2021"],
      "sentence": "Meditation reduces cortisol levels in adults.",
      "raw": "Meditation reduces cortisol \\cite{smith2021} levels...",
      "targets": [
        {
          "key": "smith2021",
          "in_bib": true,
          "bib_fields": {"title": "...", "doi": "..."},
          "resolution": {
            "status": "resolved | UNRESOLVED_IDENTITY",
            "source": "crossref|openalex|pubmed|arxiv (+combined)",
            "doi": "10.xxxx/...",
            "title": "...", "journal": "...", "year": 2021,
            "abstract": "fetched text — the ONLY evidence allowed for judging",
            "retracted_flag": false, "flagged_retraction_metadata": false,
            "is_preprint": false, "attempts": ["crossref(doi=...):hit"]
          }
        }
      ]
    }
  ],
  "integrity": {"alerts": [{"level": "RED|AMBER", "type": "RETRACTION|EXPRESSION_OF_CONCERN|UNVERIFIABLE_REFERENCE|PREPRINT_CITED", "key": "...", "detail": "..."}]}
}
```

## work/verdicts.json (written by the agent in Step 2)

```json
{
  "verdicts": [
    {
      "claim_id": "C0001",
      "grade": "SUPPORTED | PARTIAL | MISMATCH | INSUFFICIENT_EVIDENCE",
      "justification": "Abstract states: 'cortisol decreased by 18% after 8 weeks' (≤25-word quote)."
    }
  ]
}
```

Rules enforced downstream (`report` recomputes nothing about grades; it merges them):
- Every claim judged must use one of the four grades exactly.
- `justification` must contain a quoted fragment from the fetched abstract
  for SUPPORTED/PARTIAL; empty justification on SUPPORTED is treated as a
  process violation when re-audited by a supervisor agent.

## audit-report/results.json (final machine-readable output)

Contains `score` (0–100), `submission_ready` (bool), `blockers` (list),
`grades` histogram, `red_alerts`, `amber_alerts`, per-row merge of claims ×
verdicts × DOIs, plus hygiene stats. Safe to diff across revisions: same
inputs ⇒ byte-stable ordering.
