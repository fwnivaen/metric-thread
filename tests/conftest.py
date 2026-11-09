from pathlib import Path

import pytest

from metric_thread.models import Catalog

DEMO = Path(__file__).resolve().parents[1] / "src" / "metric_thread" / "demo"


@pytest.fixture
def catalog():
    return Catalog.model_validate_json((DEMO / "catalog.json").read_text(encoding="utf-8"))
