# Data contracts

All formats have `schema_version: 1`. Unknown fields are rejected. Generate full JSON Schemas with `metric-thread schema catalog`, `schema bundle` and `schema targets`.

## Catalog

`entities` contains unique `{id, name}` objects. `metrics` contains unique `{id, label, unit, definition}` objects. `expected` explicitly lists the tracked `{entity_id, metric_id}` pairs. Missing-report checks use that list for the requested month.

The complete catalog is hashed and pinned to the database. Reordering or editing it changes the fingerprint; use a new database for a changed catalog in version 0.1. Metric definitions are part of identity, so a redefined metric must not silently inherit old values.

## Source bundle

```json
{
  "schema_version": 1,
  "source": {
    "id": "cedar-october-update",
    "text": "Cedar Demo MRR for 2026-10: $12,000.",
    "uri": "https://example.com/fictional/update"
  },
  "observations": [{
    "entity_id": "cedar",
    "metric_id": "mrr",
    "period": "2026-10",
    "value": "12000",
    "unit": "USD",
    "evidence": {"start": 28, "end": 35, "quote": "$12,000"}
  }]
}
```

The example offsets must always be generated from the exact input text; the tests and exported demo show executable examples. Spans are half-open `[start, end)` in Unicode code points, after newline normalization. They select a single complete numeric token, not an entire sentence.

`supersedes` optionally names the current fact ID for a correction. `resolves` optionally names an open review item for that same entity, metric and month. Neither field bypasses any evidence check.

Values must be finite decimal strings or integers. Binary floats, booleans, excessive precision, unknown entity/metric pairs, incompatible units and evidence disagreement are not accepted. Structural errors reject the bundle before ingestion. Validly structured proposals that fail business/evidence rules enter review while other observations in the bundle may be accepted.

Source text and URI are immutable under a source ID. A changed document needs a new source ID. Accepted facts have deterministic IDs derived from the source identity/content and proposal. Exact accepted replays are ignored, including facts later superseded by corrections. Separate reports of an already populated metric/month need review or an explicit correction; corroboration is not silently merged.

## Targets

Targets can be JSON (`{"targets": [...]}`) or CSV with these exact headers:

```text
entity_id,metric_id,period,value,unit,comparison,note
```

`comparison` is `at_least`, `at_most` or `equal`. `note` identifies the supplied plan or decision behind the target. Units must match the catalog; each entity/metric/month has at most one target. The code never infers a target from update text.

Reports include the target set's fingerprint and applicable target values. A revision snapshot freezes accepted metric facts, not an external target file or the current review queue. Retain the exact target file when reproducing a comparison.

## Decisions and outputs

Ingestion returns `accepted`, `duplicates`, `review` and the current fact `revision`. Review items contain the rejected payload, source ID, reason and decision state. A dismissal has a recorded reason and leaves the original rejected proposal in history.

Reports contain one cell per expected pair, with status `met`, `missed`, `missing` or `no_target`. Values are decimal strings; `gap` means actual minus target, and `change` means actual minus the previous available period. A gap across an unreported month is not presented as a same-length monthly comparison. HTML has no remote assets or scripts, escapes source text, and only links to HTTP(S) destinations without embedded login details. CSV exports neutralize formula-leading text; JSON preserves exact values for programmatic use.
