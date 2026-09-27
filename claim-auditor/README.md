# Claim Auditor

**Audit citation-to-claim alignment in academic manuscripts — deterministically.**

The #1 pre-submission risk for students and researchers: citations that are
*real papers* but don't support the sentence they're attached to, fabricated
DOIs, and retracted references. Claim Auditor catches all three.

## What makes it different (skill-as-utility architecture)

| Layer | Who executes | What it does | Why |
|---|---|---|---|
| **Utility layer** | `scripts/audit.py` (Python stdlib only) | parse LaTeX/MD/docx → extract claims → resolve every DOI/arXiv/PMID against Crossref, OpenAlex, PubMed, arXiv → retraction/EoC checks → scoring & report | Provable, reproducible, zero hallucination |
| **Skill layer** | *your agent model* (any LLM) | grade each citing sentence vs. the fetched abstract: SUPPORTED / PARTIAL / MISMATCH / INSUFFICIENT_EVIDENCE with a ≤25-word quote requirement | The only judgment task, strictly evidence-bounded |

## Works everywhere

Follows the open `SKILL.md` convention (agentskills.org / skills.sh):
Claude Code · OpenAI Codex CLI · opencode · Cursor · Windsurf · Aider · Goose ·
Amp · Cline · any harness that can read markdown + run shell.
**No API keys, no pip installs, no MCP server.** Just Python 3.9+ and network.

## Quick start

```bash
python3 scripts/audit.py extract   --input paper.tex --bib refs.bib --out work/claims.json
python3 scripts/audit.py resolve   --work work/claims.json
python3 scripts/audit.py integrity --work work/claims.json
# agent writes work/verdicts.json (see SKILL.md Step 2)
python3 scripts/audit.py report    --work work/claims.json --verdicts work/verdicts.json --out audit-report/
```

Try it on the shipped demo (planted defects: fake DOI, overclaim, preprint, orphan ref):

```bash
cd claim-auditor
python3 scripts/audit.py extract --input examples/demo/paper.tex --bib examples/demo/refs.bib --out /tmp/work/claims.json
```

See `examples/demo/expected-output/report.md` for a full rendered report.

## Output

- `audit-report/report.md` — human report with RED/AMBER flags + per-claim table
- `audit-report/results.json` — machine-readable ledger (CI-friendly, diffable)
- Submission readiness score 0–100 with explicit blockers

## Layout

```
claim-auditor/
├── SKILL.md                  # the agent-facing instruction contract
├── scripts/audit.py          # stdlib-only deterministic engine
├── references/schemas.md     # JSON contracts (claims/verdicts/results)
├── references/api-notes.md   # registry endpoints & polite-pool rules
└── examples/demo/            # runnable end-to-end example
```

## Install into your harness

```bash
# Claude Code
cp -r claim-auditor ~/.claude/skills/
# Codex CLI / opencode / generic agentskills layout
cp -r claim-auditor ~/.agents/skills/
# Cursor
cp -r claim-auditor .cursor/skills/
```

MIT licensed. Metadata lookups use public registries; nothing leaves your
machine except DOI/title queries.
