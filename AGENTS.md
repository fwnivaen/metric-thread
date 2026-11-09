# Working in MetricLedger

This repository implements Nivaen Giritharan's standalone metric evidence workflow. The public example is entirely fictional. MetricLedger is the provisional name; the owner will choose final branding.

- Read `docs/AGENT_WORKFLOW.md` to operate it with an agent and `docs/DATA_CONTRACTS.md` before changing schemas.
- Preserve exact decimal values, numeric source checks, immutable source IDs and append-only fact/correction history.
- A model extraction is a proposal. Uncertain semantics need review; never infer absent targets, periods or unit conversions.
- Catalog changes need explicit migration design; the current release requires a new ledger for a changed catalog.
- Keep private source data, ledgers, provider credentials and generated private reports out of commits.
- Verify substantive changes with the relevant tests and `ruff`; keep the installed-wheel demo working.
- Retain the original MIT license and upstream dependency attributions.
