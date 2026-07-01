# Audit-Grade Fund Compliance Reporter

Produces an audit-defensible compliance report for the Meridian Fixed Income
Fund: for every rule it states whether the portfolio is inside or outside its
limit, by how much, and **where each figure came from** — traced through a
knowledge graph to the exact source passage. Built to satisfy five hard
constraints (reproducible, traceable, no-LLM-numbers, reconciles to Firm A,
reconfigurable to Firm B without code edits).

## Quick start

### Option A — pip
```bash
pip install -r requirements.txt
python run.py --both        # produce Firm A + Firm B reports and run all checks
```

### Option B — Docker
```bash
docker compose up --build   # runs `python run.py --both`, writes to ./output
```

Outputs land in `output/`:
`report_firm_a.xlsx`, `report_firm_b.xlsx`, `figures_firm_{a,b}.json`
(timestamp-free, for the reproducibility diff), and `audit_log.sqlite`.

### Other commands
```bash
python run.py --firm a            # Firm A only
python run.py --firm b            # Firm B only (differs by config, not code)
python run.py --firm a --llm      # Firm A with Anthropic LLM narrative
python run.py --both --llm        # both firms, with LLM narrative
python viewer.py --firm b "Largest GRE issuer"   # replay one figure end-to-end
python tests/test_determinism.py  # the five-constraint test suite
```

### Enabling the Anthropic LLM narrative (optional)

No API key is required — the system runs **fully offline** with a deterministic,
number-free narrative. The LLM layer is opt-in and only writes commentary
(constraint 3); it is never in the path that produces a number.

To turn it on, provide your own Anthropic key and pass `--llm`:

```bash
cp .env.example .env         # then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
python run.py --both --llm    # run.py auto-loads .env; the narrative comes from Claude
```

`.env` is git-ignored (never committed); a real shell `export ANTHROPIC_API_KEY`
takes precedence over it. When `--llm` is enabled the model
(`claude-sonnet-4-6`) rephrases the commentary, and whatever it returns is passed
through a numeric **firewall**: any number not already present in the computed
output is rejected and the run falls back to the deterministic narrative. The
console prints the narrative source — `llm`, `deterministic`, or
`deterministic_fallback` — so the LLM boundary is auditable at a glance.

## How it maps to the five constraints

| # | Constraint | Where |
|---|---|---|
| 1 | Reproducible (run-twice identical) | fixed timestamps, `sorted()`, `hashlib`; `figures_*.json` diff; `test_determinism` |
| 2 | Traceable through the graph | `engine/graph.py` + `engine/provenance.py`; every figure emits `graph_path` + citation; untraceable ⇒ `ERROR` |
| 3 | No LLM-produced numbers | compute layer imports no model; LLM only in `engine/narrative.py`; `firewall()` verifies it |
| 4 | Reconcile to Firm A answer key | `engine/reconcile.py`; exact match on all 13 figures (delta 0.0) |
| 5 | Reconfigure to Firm B, no code edit | `config/firm_b.yaml` = base + 3 overrides; engine is byte-identical between runs |

Plus an **append-only audit log** (`engine/audit.py`: SQLite triggers block
UPDATE/DELETE + sha256 hash chain).

## Layout

```
run.py                     entrypoint (orchestration only)
viewer.py                  bonus: per-figure replay viewer
engine/
  provenance.py            PDF -> chunks, stable chunk_ids, anchor binding
  graph.py                 the knowledge graph (rules + holdings) + query helpers
  methods.py               deterministic, parameterised computation methods
  compute.py               config loader (extends/overrides) + run + utilisation format
  reconcile.py             reconciliation + traceability + firewall
  narrative.py             LLM narrative (optional), firewalled
  report.py                fills report_template.xlsx
  audit.py                 append-only audit log
config/
  rules_meridian.yaml      human-verified rule extraction (anchored to the PDF)
  base_methods.yaml        shared method config
  firm_a.yaml / firm_b.yaml   per-firm selection (B = base + 3 overrides)
docs/                      01 flows+audit catalogue · 02 architecture · 03 RFC
sample_docs/               the provided inputs
tests/                     five-constraint test suite
.env.example               template for the optional ANTHROPIC_API_KEY (copy to .env)
```

See `docs/03_rfc.md` for the design rationale.

## Notes & scope

One-week assignment scope: happy path plus the required failure modes
(unbindable anchor ⇒ ingestion error; untraceable figure ⇒ `ERROR`; smuggled
narrative number ⇒ firewalled). The rule extraction is human-verified and
shipped as YAML; the engine re-binds each rule to the live PDF at run time, so a
tampered value or anchor is still caught. Production hardening (secrets
management, authz, WORM audit store, reviewer UI for the extraction gate) is
described in the RFC but not built.
