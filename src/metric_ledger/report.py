"""Small, portable reports. No remote assets, tracking or client-side database."""

from __future__ import annotations

import csv
import html
import io
from urllib.parse import urlsplit


def safe_link(uri: str | None) -> str | None:
    if not uri:
        return None
    try:
        parsed = urlsplit(uri)
        if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username:
            return uri
    except ValueError:
        pass
    return None


def render_html(report: dict) -> str:
    esc = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    names = {
        "met": "Met target",
        "missed": "Missed target",
        "missing": "Missing update",
        "no_target": "No target supplied",
    }
    cards = "".join(
        f'<div class="card"><span>{names[key]}</span><strong>{value}</strong></div>'
        for key, value in report["summary"].items()
    )
    rows = []
    for cell in report["cells"]:
        target = cell["target"]
        target_text = (
            f"{target['comparison'].replace('_', ' ')} {target['value']}" if target else "—"
        )
        evidence = cell.get("evidence")
        receipt = "No accepted observation"
        if evidence:
            link = safe_link(evidence["uri"])
            source = esc(evidence["source_id"])
            if link:
                source = f'<a href="{esc(link)}" rel="noreferrer noopener">{source}</a>'
            correction = (
                f"<p>Corrects fact <code>{esc(cell['supersedes'])}</code></p>"
                if cell["supersedes"]
                else ""
            )
            receipt = (
                "<details><summary>View evidence</summary>"
                f"<blockquote>{esc(evidence['context'])}</blockquote>"
                f"<p>Value span: <strong>{esc(evidence['quote'])}</strong></p>"
                f"<p>{source} · characters {evidence['start']}–{evidence['end']}</p>"
                f"<p>SHA-256 <code>{esc(evidence['sha256'])}</code></p>"
                f"<p>Fact <code>{esc(cell['fact_id'])}</code> · revision {cell['revision']}</p>"
                f"{correction}</details>"
            )
        rows.append(
            f"<tr><td><strong>{esc(cell['entity'])}</strong></td>"
            f"<td>{esc(cell['metric'])}<small>{esc(cell['definition'])}</small></td>"
            f'<td class="numeric">{esc(cell["value"] if cell["value"] is not None else "—")} '
            f"<small>{esc(cell['unit'])}</small></td><td>{esc(target_text)}</td>"
            f'<td><span class="badge {cell["status"]}">{names[cell["status"]]}</span></td>'
            f'<td class="numeric">{esc(cell.get("change") if cell.get("change") is not None else "—")}</td>'
            f"<td>{receipt}</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>MetricLedger · {esc(report["period"])}</title>
<style>
:root{{color-scheme:light dark;--bg:#f6f7fb;--panel:#fff;--ink:#182234;--muted:#526176;--line:#d8dfe9;--accent:#304bba}}
@media(prefers-color-scheme:dark){{:root{{--bg:#11151e;--panel:#191f2b;--ink:#ecf0f8;--muted:#b4bfd0;--line:#38445a;--accent:#a8baff}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif}}
main{{max-width:1280px;margin:auto;padding:32px 24px}}header{{border-bottom:1px solid var(--line);padding-bottom:24px}}
.eyebrow{{color:var(--accent);font-size:12px;letter-spacing:.12em;text-transform:uppercase;font-weight:700}}
h1{{font-size:clamp(28px,5vw,44px);line-height:1.15;margin:12px 0;letter-spacing:-.04em}}p{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:24px 0}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px}}
.card span{{display:block;color:var(--muted)}}.card strong{{font-size:32px}}.table-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:16px;background:var(--panel)}}
table{{width:100%;border-collapse:collapse;min-width:1000px}}th{{text-align:left;font-size:12px;text-transform:uppercase;color:var(--muted);letter-spacing:.04em}}
th,td{{padding:16px;vertical-align:top;border-bottom:1px solid var(--line)}}tr:last-child td{{border-bottom:0}}td:nth-child(2){{min-width:200px}}td:last-child{{min-width:200px}}
small{{display:block;color:var(--muted);font-size:12px}}.numeric{{font-variant-numeric:tabular-nums}}.badge{{white-space:nowrap;border:1px solid var(--line);padding:4px 8px;border-radius:8px;font-size:12px}}
.missed,.missing{{font-weight:700}}details{{max-width:320px}}summary{{cursor:pointer;color:var(--accent);min-height:24px}}blockquote{{margin:12px 0;padding:12px;border-left:3px solid var(--accent);white-space:pre-wrap}}
code{{font-size:11px;overflow-wrap:anywhere}}a{{color:var(--accent)}}a:focus-visible,summary:focus-visible{{outline:3px solid var(--accent);outline-offset:4px}}
footer{{font-size:12px;margin-top:24px}}@media(max-width:640px){{main{{padding:24px 16px}}.cards{{grid-template-columns:repeat(2,1fr);gap:12px}}.card{{padding:16px}}}}
</style></head><body><main>
<header><span class="eyebrow">MetricLedger / Monthly close</span><h1>Numbers with receipts.</h1>
<p>{esc(report["period"])} · Ledger revision {report["revision"]} · Every accepted number has a checked value span.</p></header>
<section aria-label="Report summary" class="cards">{cards}</section>
<h2>What needs attention?</h2><p>Expand evidence to inspect the source. Change compares with the previous available reported period; it may span a missing month.</p>
<div class="table-wrap" role="region" aria-label="Metrics, targets and source evidence" tabindex="0"><table>
<thead><tr><th>Entity</th><th>Metric</th><th>Actual</th><th>Target</th><th>Status</th><th>Change</th><th>Evidence</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<footer><p>{report["review_pending_now"]} items in the current review queue. {esc(report["review_scope"])}</p>
<p>Value spans and numeric agreement are checked; interpretation of entity, period and metric remains reviewable.
Explicit targets only. No automatic unit conversion.</p><p>Catalog SHA-256 <code>{esc(report["catalog_sha256"])}</code><br>
Target set SHA-256 <code>{esc(report["targets_sha256"] or "none")}</code></p>
<p>Created with MetricLedger by Nivaen Giritharan.</p></footer>
</main></body></html>"""


def render_csv(report: dict) -> str:
    """Neutralize spreadsheet formula prefixes in text fields on export."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "entity_id",
            "metric_id",
            "period",
            "unit",
            "value",
            "target",
            "status",
            "source_id",
            "quote",
        ]
    )
    for cell in report["cells"]:
        values = [
            cell["entity_id"],
            cell["metric_id"],
            cell["period"],
            cell["unit"],
            cell["value"],
            cell["target"]["value"] if cell["target"] else None,
            cell["status"],
            cell.get("evidence", {}).get("source_id"),
            cell.get("evidence", {}).get("quote"),
        ]
        writer.writerow(
            [
                ("'" + v if v.lstrip().startswith(("=", "+", "-", "@")) else v)
                if isinstance(v, str)
                else v
                for v in values
            ]
        )
    return output.getvalue()
