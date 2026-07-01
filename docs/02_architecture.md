# 02 — Architecture

## Component diagram

```
                      sample_docs/
        sample_fund_guidelines.pdf   sample_holdings.csv   report_template.xlsx
                 |                          |                      ^
                 v                          |                      |
   ┌──────────────────────────┐            |                      |
   │ provenance.py            │            |                      |
   │  PDF -> section chunks    │            |                      |
   │  stable chunk_ids         │            |                      |
   │  anchor binding           │            |                      |
   └─────────────┬─────────────┘           |                      |
                 │  Citations               |                      |
                 v                          v                      |
   config/rules_meridian.yaml      ┌──────────────────┐           |
   (human-verified extraction) ──> │ graph.py         │           |
                                    │  ONE knowledge    │          |
                                    │  graph (networkx) │          |
                                    │  rules + holdings │          |
                                    │  provenance on    │          |
                                    │  every node/edge  │          |
                                    └────────┬──────────┘          |
                                             │ multi-hop queries   |
                                             v                     |
   config/base_methods.yaml         ┌──────────────────┐          |
   config/firm_a.yaml ───extends──> │ methods.py        │          |
   config/firm_b.yaml ──overrides─> │ deterministic     │          |
                                    │ computation       │          |
                                    │ (NO LLM IMPORT)   │          |
                                    └────────┬──────────┘          |
                                             │ Figure objects      |
                          ┌──────────────────┼─────────────────┐   |
                          v                  v                 v   |
                  ┌─────────────┐    ┌──────────────┐   ┌──────────────┐
                  │ reconcile.py │    │ report.py    │──>│ output/*.xlsx│
                  │  vs key      │    │ fill template│   └──────────────┘
                  │  traceability│    └──────────────┘
                  │  firewall    │
                  └──────┬───────┘            ┌────────────────────────┐
                         │                    │ narrative.py           │
                         │  <─── firewall ─── │  LLM (optional) — the   │
                         │                    │  ONLY LLM touch-point;  │
                         │                    │  commentary only        │
                         │                    └────────────────────────┘
                         v
                  ┌──────────────────────────────────────────────┐
                  │ audit.py — append-only SQLite + hash chain    │
                  │ records every step above (immutable)          │
                  └──────────────────────────────────────────────┘
```

## The graph (Phase 2 model)

Node kinds: `Fund`, `AssetClass`, `Limit`, `Aggregate`, `Cap`, `Floor`,
`RiskLimit`, `Owner`, `Retention`, `Position`, `Issuer`, `ParentIssuer`.

Edge kinds: `PERMITS` (Fund→AssetClass), `HAS_LIMIT` (AssetClass→Limit),
`CONTRIBUTES_TO` (AssetClass→Aggregate), `CONSTRAINED_BY` (Fund→Cap/Floor/RiskLimit),
`ON_BREACH` (Limit/Cap/Floor/Aggregate/RiskLimit→Owner, carrying the breach
action), `RETAINS` (Fund→Retention), `IN_ASSET_CLASS` (Position→AssetClass),
`ISSUED_BY` (Position→Issuer), `ROLLS_UP_TO` (Issuer→ParentIssuer).

Every node and edge carries
`provenance = {source_doc, page, chunk_id, section, ingested_at, extraction_confidence}`.

Multi-hop examples actually answered by traversal (not by re-reading the doc):
- *"largest GRE issuer at parent level"*: `Position -[:ISSUED_BY]-> Issuer
  -[:ROLLS_UP_TO]-> ParentIssuer`, grouped and tested against `Cap:gre_issuer`.
- *"if portfolio duration exceeds its limit, what happens and who is notified?"*:
  `RiskLimit:modified_duration -[:ON_BREACH]-> Owner:Portfolio Manager` (the
  edge carries the action "PM notification within 1h"). See `graph.breach_response`.
- *"how long are investor-facing reports retained?"*: `Fund -[:RETAINS]->
  Retention:investor_facing_reports` (10 years). See `graph.retention_for`.

## Data vs engine (why Firm B needs no code edit)

```
ENGINE (never edited per firm)          DATA (per firm)
  provenance.py                           config/rules_meridian.yaml   (the fund)
  graph.py                                config/base_methods.yaml     (defaults)
  methods.py  (parameterised methods)     config/firm_a.yaml           (Firm A)
  compute.py  (reads config)              config/firm_b.yaml           (3 overrides)
  reconcile.py / report.py / audit.py
```

Switching firm = pointing the entrypoint at a different YAML. The method
registry is fixed; the parameters change.

## Determinism

Fixed ingestion timestamp (`FROZEN_AS_OF`), `sorted()` on every set traversal,
`hashlib` (not the salted built-in `hash()`) for chunk/audit ids, and a
timestamp-free `figures.json` for the run-twice diff. See `tests/test_determinism.py`.
