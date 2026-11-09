import pytest
from conftest import DEMO

from metric_ledger.extract import extract_bundle, replay_model
from metric_ledger.ledger import Ledger
from metric_ledger.models import Bundle, Source

pytest.importorskip("langextract")


def test_real_langextract_alignment_and_duckdb_ingestion(catalog):
    fixture = Bundle.model_validate_json((DEMO / "bundle-01.json").read_text(encoding="utf-8"))
    result = extract_bundle(fixture.source, catalog, model=replay_model(fixture.observations))
    assert not result.ungrounded
    assert len(result.bundle.observations) == 3
    with Ledger(":memory:", catalog) as ledger:
        accepted = ledger.ingest(result.bundle)
        assert len(accepted["accepted"]) == 3
        assert not accepted["review"]


def test_model_example_leakage_is_not_accepted(catalog):
    fixture = Bundle.model_validate_json((DEMO / "bundle-01.json").read_text(encoding="utf-8"))
    unrelated = Source(
        id="no-metrics", text="No numerical business update is available in this message."
    )
    result = extract_bundle(unrelated, catalog, model=replay_model(fixture.observations))
    with Ledger(":memory:", catalog) as ledger:
        assert not ledger.ingest(result.bundle)["accepted"]


def test_model_numeric_disagreement_cannot_be_accepted(catalog):
    fixture = Bundle.model_validate_json((DEMO / "bundle-01.json").read_text(encoding="utf-8"))
    wrong = fixture.observations[0].model_copy(update={"value": fixture.observations[0].value + 1})
    result = extract_bundle(fixture.source, catalog, model=replay_model([wrong]))
    with Ledger(":memory:", catalog) as ledger:
        outcome = ledger.ingest(result.bundle)
        assert not outcome["accepted"] and outcome["review"]
