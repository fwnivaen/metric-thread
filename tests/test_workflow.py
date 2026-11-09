from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from metric_ledger.cli import main, run_demo
from metric_ledger.ledger import Ledger, LedgerError
from metric_ledger.models import Bundle, Catalog, TargetSet, value_from_quote
from metric_ledger.report import render_csv, render_html, safe_link


def bundle(source_id="source-1", token="$12,000", value="12000", **changes):
    text = f"Fictional Cedar Demo: MRR for 2026-10 was {token}."
    start = text.index(token)
    payload = {
        "source": {"id": source_id, "text": text},
        "observations": [
            {
                "entity_id": "cedar",
                "metric_id": "mrr",
                "period": "2026-10",
                "value": value,
                "unit": "USD",
                "evidence": {"start": start, "end": start + len(token), "quote": token},
                **changes,
            }
        ],
    }
    return Bundle.model_validate(payload)


def targets(value="13000", unit="USD", comparison="at_least"):
    return TargetSet.model_validate(
        {
            "targets": [
                {
                    "entity_id": "cedar",
                    "metric_id": "mrr",
                    "period": "2026-10",
                    "value": value,
                    "unit": unit,
                    "comparison": comparison,
                    "note": "Fictional agreed plan",
                }
            ]
        }
    )


def cedar_cell(report):
    return next(c for c in report["cells"] if c["entity_id"] == "cedar" and c["metric_id"] == "mrr")


def test_full_offline_demo_records_corrections_missing_reports_and_rejections(tmp_path):
    result = run_demo(tmp_path / "demo")
    assert result["summary"] == {"met": 3, "missed": 3, "missing": 3, "no_target": 0}
    assert result["revision"] == 16
    assert result["review_pending"] == 1
    assert result["model_inference_performed"] is False
    report = json.loads((tmp_path / "demo/report.json").read_text())
    assert cedar_cell(report)["value"] == "25000"
    assert cedar_cell(report)["change"] == "3000"
    history = json.loads((tmp_path / "demo/history.json").read_text())
    assert history[-1]["supersedes"] == history[9]["id"]
    assert len(history) == 16
    assert (tmp_path / "demo/report.csv").exists()


def test_replay_survives_reopen_and_decimal_formatting(tmp_path, catalog):
    path = tmp_path / "ledger.duckdb"
    first = bundle()
    with Ledger(path, catalog) as ledger:
        result = ledger.ingest(first)
    payload = first.model_dump(mode="json")
    payload["observations"][0]["value"] = "12000.000"
    with Ledger(path, catalog) as ledger:
        again = ledger.ingest(Bundle.model_validate(payload))
        assert again["accepted"] == []
        assert again["duplicates"] == result["accepted"]
        assert ledger.revision == 1


def test_explicit_correction_preserves_prior_snapshot(catalog):
    with Ledger(":memory:", catalog) as ledger:
        original = ledger.ingest(bundle())["accepted"][0]
        changed = ledger.ingest(bundle("correction", "$14,000", "14000", supersedes=original))
        assert changed["revision"] == 2
        assert cedar_cell(ledger.report("2026-10"))["value"] == "14000"
        assert cedar_cell(ledger.report("2026-10", as_of=1))["value"] == "12000"
        assert ledger.history()[0]["value"] == "12000"
        assert ledger.history()[1]["supersedes"] == original
        assert ledger.report("2026-10", as_of=0)["summary"]["missing"] == 9


def test_stale_correction_cannot_branch_history(catalog):
    with Ledger(":memory:", catalog) as ledger:
        first = ledger.ingest(bundle())["accepted"][0]
        ledger.ingest(bundle("second", "$14,000", "14000", supersedes=first))
        result = ledger.ingest(bundle("third", "$15,000", "15000", supersedes=first))
        assert result["review"] and not result["accepted"]
        assert cedar_cell(ledger.report("2026-10"))["value"] == "14000"


@pytest.mark.parametrize(
    "change",
    [{"unit": "EUR"}, {"entity_id": "unknown"}, {"metric_id": "unknown"}, {"value": "9999"}],
)
def test_unjustified_proposals_go_to_review_without_becoming_facts(catalog, change):
    with Ledger(":memory:", catalog) as ledger:
        result = ledger.ingest(bundle(**change))
        assert len(result["review"]) == 1
        assert ledger.revision == 0
        assert ledger.history() == []


def test_bad_offsets_and_unicode_are_not_silently_fixed(catalog):
    good = bundle()
    payload = good.model_dump(mode="json")
    payload["source"]["text"] = "🧾\n" + payload["source"]["text"]
    with Ledger(":memory:", catalog) as ledger:
        assert ledger.ingest(Bundle.model_validate(payload))["review"]
    payload["observations"][0]["evidence"]["start"] += 2
    payload["observations"][0]["evidence"]["end"] += 2
    with Ledger(":memory:", catalog) as ledger:
        assert ledger.ingest(Bundle.model_validate(payload))["accepted"]


def test_unreferenced_replacement_is_reviewed(catalog):
    with Ledger(":memory:", catalog) as ledger:
        ledger.ingest(bundle())
        result = ledger.ingest(bundle("new-report", "$13,000", "13000"))
        assert "supersedes" in result["review"][0]["reason"]
        assert ledger.revision == 1


def test_source_id_is_immutable_and_conflict_rolls_back(catalog):
    with Ledger(":memory:", catalog) as ledger:
        ledger.ingest(bundle())
        with pytest.raises(LedgerError, match="Source ID"):
            ledger.ingest(bundle(token="$20,000", value="20000"))
        assert ledger.revision == 1
        assert ledger.db.execute("SELECT count(*) FROM sources").fetchone()[0] == 1


def test_revision_guard_prevents_write_even_of_new_source(catalog):
    with Ledger(":memory:", catalog) as ledger:
        ledger.ingest(bundle(), expected_revision=0)
        with pytest.raises(LedgerError, match="Stale revision"):
            ledger.ingest(bundle("stale"), expected_revision=0)
        assert ledger.db.execute("SELECT count(*) FROM sources").fetchone()[0] == 1


def test_catalog_drift_requires_an_explicit_new_ledger(tmp_path, catalog):
    path = tmp_path / "ledger.duckdb"
    with Ledger(path, catalog) as ledger:
        ledger.ingest(bundle())
    payload = catalog.model_dump()
    payload["metrics"][0]["definition"] = "A materially different metric"
    with pytest.raises(LedgerError, match="Catalog differs"):
        Ledger(path, Catalog.model_validate(payload))


def test_review_is_resolved_only_by_an_accepted_fact_for_the_same_key(catalog):
    with Ledger(":memory:", catalog) as ledger:
        rejected = ledger.ingest(bundle(value="9999"))["review"][0]["id"]
        wrong = ledger.ingest(bundle("wrong-key", entity_id="orbit", resolves=rejected))
        assert wrong["review"] and ledger.revision == 0
        accepted = ledger.ingest(bundle("fixed", resolves=rejected))
        assert accepted["accepted"]
        assert rejected not in {r["id"] for r in ledger.review()}


@pytest.mark.parametrize(
    "value,comparison,status",
    [
        ("12000", "equal", "met"),
        ("12000.01", "equal", "missed"),
        ("12000", "at_most", "met"),
        ("11999.99", "at_most", "missed"),
        ("12000", "at_least", "met"),
    ],
)
def test_targets_use_decimal_arithmetic_and_explicit_comparison(catalog, value, comparison, status):
    with Ledger(":memory:", catalog) as ledger:
        ledger.ingest(bundle())
        cell = cedar_cell(ledger.report("2026-10", targets(value, comparison=comparison)))
        assert cell["status"] == status
        assert Decimal(cell["gap"]) == Decimal("12000") - Decimal(value)


def test_no_supplied_target_remains_unavailable(catalog):
    with Ledger(":memory:", catalog) as ledger:
        ledger.ingest(bundle())
        cell = cedar_cell(ledger.report("2026-10"))
        assert cell["status"] == "no_target" and cell["target"] is None
        with pytest.raises(LedgerError, match="unit"):
            ledger.report("2026-10", targets(unit="EUR"))


@pytest.mark.parametrize("as_of", [-1, 2, True])
def test_invalid_snapshot_is_rejected(catalog, as_of):
    with Ledger(":memory:", catalog) as ledger:
        ledger.ingest(bundle())
        with pytest.raises(LedgerError):
            ledger.report("2026-10", as_of=as_of)


@pytest.mark.parametrize("value", [float("nan"), 0.1, True, "NaN", "Infinity", "0.123456789"])
def test_non_finite_or_lossy_values_cannot_enter_a_bundle(value):
    with pytest.raises(ValidationError):
        bundle(value=value)


@pytest.mark.parametrize(
    "quote,unit,expected",
    [
        ("$12.5k", "USD", "12500"),
        ("€2M", "EUR", "2000000"),
        ("-3.5%", "percent", "-3.5"),
        ("0", "count", "0"),
        ("1,234.50", "USD", "1234.50"),
    ],
)
def test_supported_numeric_evidence(quote, unit, expected):
    assert value_from_quote(quote, unit) == Decimal(expected)


@pytest.mark.parametrize(
    "quote,unit", [("12,34", "count"), ("$12", "EUR"), ("25%", "ratio"), ("Revenue 12", "USD")]
)
def test_unsupported_evidence_requires_review(quote, unit):
    with pytest.raises(ValueError):
        value_from_quote(quote, unit)


def test_report_escapes_untrusted_text_and_rejects_active_links(catalog):
    source = bundle().model_dump(mode="json")
    source["source"]["text"] += '<script>alert("bad")</script>'
    source["source"]["uri"] = 'javascript:alert("bad")'
    with Ledger(":memory:", catalog) as ledger:
        ledger.ingest(Bundle.model_validate(source))
        html = render_html(ledger.report("2026-10"))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "javascript:" not in html
        assert "Content-Security-Policy" in html


def test_link_policy():
    assert safe_link("https://example.com/update") == "https://example.com/update"
    for uri in [
        "file:///etc/passwd",
        "data:text/html,hello",
        "https://user:password@example.com/",
        "https://[",
    ]:
        assert safe_link(uri) is None


def test_csv_formula_prefixes_are_neutralized(catalog):
    with Ledger(":memory:", catalog) as ledger:
        ledger.ingest(bundle())
        report = ledger.report("2026-10")
        report["cells"][0]["unit"] = "=IMPORTXML(A1)"
        assert "'=IMPORTXML" in render_csv(report)


def test_cli_json_contract_and_fail_on_review(tmp_path, catalog, capsys):
    cfg, data = tmp_path / "catalog.json", tmp_path / "bundle.json"
    cfg.write_text(catalog.model_dump_json())
    data.write_text(bundle(value="9999").model_dump_json())
    code = main(
        [
            "ingest",
            "--catalog",
            str(cfg),
            "--db",
            str(tmp_path / "db.duckdb"),
            "--bundle",
            str(data),
            "--fail-on-review",
        ]
    )
    assert code == 3
    result = json.loads(capsys.readouterr().out)
    assert result["revision"] == 0 and result["review"]


def test_demo_does_not_overwrite_an_existing_workspace(tmp_path):
    directory = tmp_path / "already"
    directory.mkdir()
    sentinel = directory / "keep.txt"
    sentinel.write_text("preserve me")
    assert main(["demo", "--output", str(directory)]) == 2
    assert sentinel.read_text() == "preserve me"
