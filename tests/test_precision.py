from decimal import Decimal

from metric_ledger.ledger import Ledger
from metric_ledger.models import Bundle, value_from_quote


def test_source_number_cannot_be_rounded_to_match_a_different_candidate(catalog):
    quote = "$12345678901234567890.123456789"
    value = "12345678901234567890.12345679"
    assert value_from_quote(quote, "USD") == Decimal("12345678901234567890.123456789")
    source = "Fictional metric: " + quote
    bundle = Bundle.model_validate(
        {
            "source": {"id": "precision", "text": source},
            "observations": [
                {
                    "entity_id": "cedar",
                    "metric_id": "mrr",
                    "period": "2026-10",
                    "value": value,
                    "unit": "USD",
                    "evidence": {
                        "start": len("Fictional metric: "),
                        "end": len(source),
                        "quote": quote,
                    },
                }
            ],
        }
    )
    with Ledger(":memory:", catalog) as ledger:
        result = ledger.ingest(bundle)
        assert result["review"] and not result["accepted"]
