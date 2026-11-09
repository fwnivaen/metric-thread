"""Strict, portable data contracts. Values use decimal strings, never binary floats."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$")]
Period = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


def decimal_input(value: object) -> object:
    if isinstance(value, (float, bool)):
        raise ValueError("Use a decimal string or integer, not a float or boolean")
    return value


Number = Annotated[
    Decimal,
    BeforeValidator(decimal_input),
    Field(max_digits=28, decimal_places=8, allow_inf_nan=False),
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Entity(Contract):
    id: Identifier
    name: str = Field(min_length=1, max_length=200)


class Metric(Contract):
    id: Identifier
    label: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=32)
    definition: str = Field(min_length=1, max_length=2000)


class Expected(Contract):
    entity_id: Identifier
    metric_id: Identifier


class Catalog(Contract):
    schema_version: Literal[1] = 1
    entities: tuple[Entity, ...] = Field(min_length=1)
    metrics: tuple[Metric, ...] = Field(min_length=1)
    expected: tuple[Expected, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_and_known(self) -> Catalog:
        entities = {e.id for e in self.entities}
        metrics = {m.id for m in self.metrics}
        if len(entities) != len(self.entities) or len(metrics) != len(self.metrics):
            raise ValueError("Entity and metric IDs must be unique")
        pairs = {(e.entity_id, e.metric_id) for e in self.expected}
        if len(pairs) != len(self.expected):
            raise ValueError("Expected entity/metric pairs must be unique")
        if any(e not in entities or m not in metrics for e, m in pairs):
            raise ValueError("Expected pairs must reference registered entities and metrics")
        return self

    @property
    def fingerprint(self) -> str:
        return digest(self.model_dump(mode="json"))


class Source(Contract):
    id: Identifier
    text: str = Field(min_length=1, max_length=2_000_000)
    uri: str | None = Field(default=None, max_length=2000)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


class Evidence(Contract):
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    quote: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def ordered(self) -> Evidence:
        if self.end <= self.start:
            raise ValueError("Evidence end must be after start")
        return self


class Observation(Contract):
    entity_id: Identifier
    metric_id: Identifier
    period: Period
    value: Number
    unit: str = Field(min_length=1, max_length=32)
    evidence: Evidence
    supersedes: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    resolves: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class Bundle(Contract):
    schema_version: Literal[1] = 1
    source: Source
    observations: list[Observation] = Field(max_length=10_000)


class Target(Contract):
    entity_id: Identifier
    metric_id: Identifier
    period: Period
    value: Number
    unit: str = Field(min_length=1, max_length=32)
    comparison: Literal["at_least", "at_most", "equal"] = "at_least"
    note: str = Field(min_length=1, max_length=2000)


class TargetSet(Contract):
    schema_version: Literal[1] = 1
    targets: list[Target]

    @model_validator(mode="after")
    def unique(self) -> TargetSet:
        keys = {(t.entity_id, t.metric_id, t.period) for t in self.targets}
        if len(keys) != len(self.targets):
            raise ValueError("Duplicate targets for the same entity, metric and period")
        return self


def digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def number(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


TOKEN = re.compile(
    r"(?P<sign>[+-]?)(?P<currency>[$€£]?)(?P<amount>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?P<suffix>[kKmMbB%]?)"
)


def value_from_quote(quote: str, unit: str) -> Decimal:
    """Check a complete numeric token, including supported currency/scale notation."""
    match = TOKEN.fullmatch(quote.strip())
    if not match:
        raise ValueError("Evidence must select one numeric token, e.g. $12,000 or 25%")
    symbol = match["currency"]
    if symbol and {"$": "USD", "€": "EUR", "£": "GBP"}[symbol] != unit:
        raise ValueError("Currency symbol disagrees with the declared unit")
    suffix = match["suffix"].lower()
    if suffix == "%" and unit != "percent":
        raise ValueError("Percent evidence requires unit 'percent' (25% means 25, not 0.25)")
    multiplier = {"k": 1000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    # Keep all source digits: context rounding must never make a different value agree.
    with localcontext() as context:
        context.prec = max(50, len(match["amount"]) + 16)
        return Decimal(match["sign"] + match["amount"].replace(",", "")) * multiplier
