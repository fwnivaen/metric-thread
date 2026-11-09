import json

import pytest
from conftest import DEMO

from metric_ledger.cli import main
from metric_ledger.ledger import Ledger, LedgerError
from metric_ledger.models import Bundle


def test_batch_can_be_retried_without_duplicating_corrections(tmp_path, capsys):
    args = [
        "run",
        "--catalog",
        str(DEMO / "catalog.json"),
        "--db",
        str(tmp_path / "db.duckdb"),
        "--inbox",
        str(DEMO),
        "--pattern",
        "bundle-*.json",
        "--period",
        "2026-10",
        "--targets",
        str(DEMO / "targets.csv"),
        "--output",
        str(tmp_path / "reports"),
        "--fail-on-review",
    ]
    assert main(args) == 3
    first = json.loads(capsys.readouterr().out)
    assert first["revision"] == 16
    assert main(args) == 3
    second = json.loads(capsys.readouterr().out)
    assert second["revision"] == 16
    assert all(not outcome["accepted"] for outcome in second["outcomes"])
    assert len(json.loads((tmp_path / "reports/history.json").read_text())) == 16


def test_structurally_invalid_batch_is_rejected_before_database_creation(tmp_path, capsys):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "first.json").write_text((DEMO / "bundle-01.json").read_text())
    (inbox / "second.json").write_text('{"not_a_bundle": true}')
    database = tmp_path / "new.duckdb"
    assert (
        main(
            [
                "run",
                "--catalog",
                str(DEMO / "catalog.json"),
                "--db",
                str(database),
                "--inbox",
                str(inbox),
                "--period",
                "2026-10",
                "--output",
                str(tmp_path / "out"),
            ]
        )
        == 2
    )
    assert not database.exists()
    assert json.loads(capsys.readouterr().err)["error"]


def test_dismissal_is_a_recorded_decision_not_an_accepted_value(catalog):
    fixture = Bundle.model_validate_json((DEMO / "bundle-07.json").read_text())
    with Ledger(":memory:", catalog) as ledger:
        review_id = ledger.ingest(fixture)["review"][0]["id"]
        ledger.dismiss(review_id, "Extraction was wrong; no new metric should be added")
        assert ledger.review() == []
        assert ledger.history() == []
        all_items = ledger.review(include_closed=True)
        assert all_items[0]["state"] == "dismissed"
        assert all_items[0]["dismissal_reason"]
        with pytest.raises(LedgerError, match="already closed"):
            ledger.dismiss(review_id, "Duplicate request")
