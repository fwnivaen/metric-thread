"""Append-only evidence history, explicit corrections and deterministic reporting."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import localcontext
from pathlib import Path

import duckdb

from .models import (
    Bundle,
    Catalog,
    Observation,
    Source,
    TargetSet,
    digest,
    number,
    value_from_quote,
)


class LedgerError(ValueError):
    """An actionable input, catalog or revision conflict."""


class Ledger:
    def __init__(self, path: str | Path, catalog: Catalog):
        self.catalog = catalog
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = duckdb.connect(str(path), config={"enable_external_access": "false"})
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS metadata (key VARCHAR PRIMARY KEY, value VARCHAR)"
        )
        stored = self.db.execute("SELECT value FROM metadata WHERE key='catalog'").fetchone()
        if stored and stored[0] != catalog.fingerprint:
            self.db.close()
            raise LedgerError(
                "Catalog differs from this ledger. Use a new database for new definitions."
            )
        if not stored:
            self.db.execute("INSERT INTO metadata VALUES ('catalog', ?)", [catalog.fingerprint])
            self.db.execute("INSERT INTO metadata VALUES ('schema', '1')")
        version = self.db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
        if not version or version[0] != "1":
            self.db.close()
            raise LedgerError("Unsupported ledger schema")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id VARCHAR PRIMARY KEY, fingerprint VARCHAR NOT NULL, text VARCHAR NOT NULL,
                uri VARCHAR, recorded_at VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS facts (
                id VARCHAR PRIMARY KEY, revision BIGINT UNIQUE NOT NULL,
                entity_id VARCHAR NOT NULL, metric_id VARCHAR NOT NULL, period VARCHAR NOT NULL,
                value DECIMAL(28,8) NOT NULL, unit VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL, span_start BIGINT, span_end BIGINT,
                quote VARCHAR NOT NULL, supersedes VARCHAR UNIQUE, recorded_at VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review (
                id VARCHAR PRIMARY KEY, source_id VARCHAR, payload VARCHAR, reason VARCHAR,
                recorded_at VARCHAR
            );
            CREATE TABLE IF NOT EXISTS resolutions (
                review_id VARCHAR PRIMARY KEY, fact_id VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dismissals (
                review_id VARCHAR PRIMARY KEY, reason VARCHAR NOT NULL, recorded_at VARCHAR NOT NULL
            );
        """)

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *_: object) -> None:
        self.db.close()

    @property
    def revision(self) -> int:
        return self.db.execute("SELECT coalesce(max(revision), 0) FROM facts").fetchone()[0]

    def _rows(self, query: str, params: list | None = None) -> list[dict]:
        cursor = self.db.execute(query, params or [])
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def _reason(self, source: Source, observation: Observation) -> str | None:
        metric = next((m for m in self.catalog.metrics if m.id == observation.metric_id), None)
        if not metric or observation.entity_id not in {e.id for e in self.catalog.entities}:
            return "Unknown entity or metric; update the input to use registered IDs"
        if (observation.entity_id, observation.metric_id) not in {
            (e.entity_id, e.metric_id) for e in self.catalog.expected
        }:
            return "This entity/metric pair is not configured for tracking"
        if observation.unit != metric.unit:
            return "Unit differs from the metric definition; no implicit unit conversion"
        e = observation.evidence
        if e.end > len(source.text) or source.text[e.start : e.end] != e.quote:
            return "Evidence offsets and quote do not match the immutable source text"
        try:
            if value_from_quote(e.quote, observation.unit) != observation.value:
                return "Reported value disagrees with the number in the source span"
        except ValueError as exc:
            return str(exc)
        key = [observation.entity_id, observation.metric_id, observation.period]
        current = self.db.execute(
            """
            SELECT f.id FROM facts f WHERE entity_id=? AND metric_id=? AND period=?
            AND NOT EXISTS (SELECT 1 FROM facts newer WHERE newer.supersedes=f.id)
        """,
            key,
        ).fetchone()
        if observation.supersedes:
            if not current or current[0] != observation.supersedes:
                return (
                    "Correction must supersede the current fact for the same "
                    "entity, metric and period"
                )
        elif current:
            return (
                "A current fact already exists; supply its ID as supersedes "
                "for an explicit correction"
            )
        if observation.resolves:
            review = self.db.execute(
                """
                SELECT payload FROM review WHERE id=? AND NOT EXISTS
                (SELECT 1 FROM resolutions WHERE review_id=review.id)
                AND NOT EXISTS (SELECT 1 FROM dismissals WHERE review_id=review.id)
            """,
                [observation.resolves],
            ).fetchone()
            if not review:
                return "Review item does not exist or has already been resolved"
            original = json.loads(review[0])
            if any(
                original[k] != getattr(observation, k) for k in ("entity_id", "metric_id", "period")
            ):
                return "A review resolution must address the same entity, metric and period"
        return None

    def ingest(self, bundle: Bundle, expected_revision: int | None = None) -> dict:
        """Validate each fact; business-rule failures enter review."""
        accepted, duplicates, queued = [], [], []
        timestamp = datetime.now(UTC).isoformat()
        self.db.execute("BEGIN TRANSACTION")
        try:
            if expected_revision is not None and expected_revision != self.revision:
                raise LedgerError(
                    f"Stale revision: expected {expected_revision}, current {self.revision}"
                )
            source = bundle.source
            existing = self.db.execute(
                "SELECT fingerprint, uri FROM sources WHERE id=?", [source.id]
            ).fetchone()
            if existing and existing != (source.fingerprint, source.uri):
                raise LedgerError(
                    "Source ID already exists with different text or URI; use a new ID"
                )
            if not existing:
                self.db.execute(
                    "INSERT INTO sources VALUES (?, ?, ?, ?, ?)",
                    [source.id, source.fingerprint, source.text, source.uri, timestamp],
                )
            for observation in bundle.observations:
                payload = observation.model_dump(mode="json")
                payload["value"] = number(observation.value)
                fact_id = digest(
                    {"source": source.id, "sha256": source.fingerprint, "fact": payload}
                )
                if self.db.execute("SELECT 1 FROM facts WHERE id=?", [fact_id]).fetchone():
                    duplicates.append(fact_id)
                    continue
                reason = self._reason(source, observation)
                if reason:
                    review_id = digest({"fact": fact_id, "reason": reason})
                    self.db.execute(
                        "INSERT OR IGNORE INTO review VALUES (?, ?, ?, ?, ?)",
                        [review_id, source.id, json.dumps(payload), reason, timestamp],
                    )
                    queued.append({"id": review_id, "reason": reason})
                    continue
                e = observation.evidence
                self.db.execute(
                    "INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        fact_id,
                        self.revision + 1,
                        observation.entity_id,
                        observation.metric_id,
                        observation.period,
                        observation.value,
                        observation.unit,
                        source.id,
                        e.start,
                        e.end,
                        e.quote,
                        observation.supersedes,
                        timestamp,
                    ],
                )
                if observation.resolves:
                    self.db.execute(
                        "INSERT INTO resolutions VALUES (?, ?)", [observation.resolves, fact_id]
                    )
                accepted.append(fact_id)
            revision = self.revision
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "review": queued,
            "revision": revision,
        }

    def review(self, include_closed: bool = False) -> list[dict]:
        rows = self._rows("""
            SELECT r.*, x.fact_id AS resolved_fact_id, d.reason AS dismissal_reason,
                   CASE WHEN x.review_id IS NOT NULL THEN 'resolved'
                        WHEN d.review_id IS NOT NULL THEN 'dismissed' ELSE 'open' END AS state
            FROM review r LEFT JOIN resolutions x ON x.review_id=r.id
            LEFT JOIN dismissals d ON d.review_id=r.id ORDER BY r.recorded_at, r.id
        """)
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return rows if include_closed else [r for r in rows if r["state"] == "open"]

    def dismiss(self, review_id: str, reason: str) -> None:
        if not reason.strip() or len(reason) > 2000:
            raise LedgerError("A nonempty dismissal reason of at most 2000 characters is required")
        if review_id not in {row["id"] for row in self.review()}:
            raise LedgerError("Review item does not exist or is already closed")
        self.db.execute(
            "INSERT INTO dismissals VALUES (?, ?, ?)",
            [review_id, reason, datetime.now(UTC).isoformat()],
        )

    def history(self) -> list[dict]:
        rows = self._rows("SELECT * FROM facts ORDER BY revision")
        for row in rows:
            row["value"] = number(row["value"])
        return rows

    def report(
        self, period: str, targets: TargetSet | None = None, as_of: int | None = None
    ) -> dict:
        from pydantic import TypeAdapter

        from .models import Period

        TypeAdapter(Period).validate_python(period)
        snapshot = self.revision if as_of is None else as_of
        if (
            isinstance(snapshot, bool)
            or not isinstance(snapshot, int)
            or not 0 <= snapshot <= self.revision
        ):
            raise LedgerError("Snapshot must be an integer between 0 and the current revision")
        rows = self._rows(
            """
            WITH active AS (
                SELECT f.* FROM facts f WHERE f.revision <= ? AND NOT EXISTS
                (SELECT 1 FROM facts child WHERE child.supersedes=f.id AND child.revision <= ?)
            ), changes AS (
                SELECT *, lag(value) OVER (
                    PARTITION BY entity_id, metric_id ORDER BY period
                ) AS previous_value FROM active
            )
            SELECT f.*, s.uri, s.fingerprint AS source_sha256, s.text AS source_text
            FROM changes f JOIN sources s ON s.id=f.source_id WHERE f.period=?
            ORDER BY f.entity_id, f.metric_id
        """,
            [snapshot, snapshot, period],
        )
        lookup = {(r["entity_id"], r["metric_id"]): r for r in rows}
        target_map = {}
        entities = {e.id: e.name for e in self.catalog.entities}
        metrics = {m.id: m for m in self.catalog.metrics}
        expected = {(e.entity_id, e.metric_id) for e in self.catalog.expected}
        for target in targets.targets if targets else []:
            if (target.entity_id, target.metric_id) not in expected:
                raise LedgerError("Target uses an untracked entity/metric pair")
            if target.unit != metrics[target.metric_id].unit:
                raise LedgerError("Target unit differs from the metric definition")
            if target.period == period:
                target_map[target.entity_id, target.metric_id] = target
        cells = []
        for entity, metric in sorted(expected):
            fact = lookup.get((entity, metric))
            target = target_map.get((entity, metric))
            cell = {
                "entity_id": entity,
                "entity": entities[entity],
                "metric_id": metric,
                "metric": metrics[metric].label,
                "definition": metrics[metric].definition,
                "period": period,
                "unit": metrics[metric].unit,
                "status": "missing" if fact is None else "no_target",
                "value": None,
                "target": target.model_dump(mode="json") if target else None,
            }
            if fact:
                value = fact["value"]
                cell.update(
                    {
                        "value": number(value),
                        "fact_id": fact["id"],
                        "revision": fact["revision"],
                        "supersedes": fact["supersedes"],
                        "evidence": {
                            "source_id": fact["source_id"],
                            "uri": fact["uri"],
                            "sha256": fact["source_sha256"],
                            "start": fact["span_start"],
                            "end": fact["span_end"],
                            "quote": fact["quote"],
                            "context": fact["source_text"][
                                max(0, fact["span_start"] - 90) : fact["span_end"] + 90
                            ],
                        },
                    }
                )
                with localcontext() as ctx:
                    ctx.prec = 40
                    cell["change"] = (
                        number(value - fact["previous_value"])
                        if fact["previous_value"] is not None
                        else None
                    )
                    if target:
                        met = {
                            "at_least": value >= target.value,
                            "at_most": value <= target.value,
                            "equal": value == target.value,
                        }[target.comparison]
                        cell.update(
                            {
                                "status": "met" if met else "missed",
                                "gap": number(value - target.value),
                            }
                        )
            cells.append(cell)
        return {
            "schema_version": 1,
            "period": period,
            "revision": snapshot,
            "catalog_sha256": self.catalog.fingerprint,
            "targets_sha256": digest(targets.model_dump(mode="json")) if targets else None,
            "summary": {
                s: sum(c["status"] == s for c in cells)
                for s in ("met", "missed", "missing", "no_target")
            },
            "cells": cells,
            "review_pending_now": len(self.review()),
            "review_scope": (
                "Current review queue; metric values above respect the requested revision."
            ),
        }
