"""Machine-readable commands for agents and a complete offline demo for people."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from .ledger import Ledger
from .models import Bundle, Catalog, Source, TargetSet
from .report import render_csv, render_html


def read_model(path: str | Path, model):
    return model.model_validate_json(Path(path).read_text(encoding="utf-8"))


def read_targets(path: str | None) -> TargetSet | None:
    if not path:
        return None
    file = Path(path)
    if file.suffix.lower() == ".csv":
        with file.open(encoding="utf-8-sig", newline="") as handle:
            return TargetSet.model_validate({"targets": list(csv.DictReader(handle))})
    return read_model(path, TargetSet)


def emit(value: object, output: str | Path | None = None) -> None:
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def initialize(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("Choose an empty or new workspace; existing files are preserved")
    path.mkdir(parents=True, exist_ok=True)
    root = resources.files("metric_thread").joinpath("demo")
    for item in root.iterdir():
        if item.is_file():
            (path / item.name).write_text(item.read_text(encoding="utf-8"), encoding="utf-8")


def export_report(ledger: Ledger, period: str, targets: TargetSet | None, path: Path) -> dict:
    report = ledger.report(period, targets)
    emit(report, path / "report.json")
    emit(render_html(report), path / "report.html")
    emit(render_csv(report), path / "report.csv")
    emit(ledger.history(), path / "history.json")
    emit(ledger.review(include_closed=True), path / "review.json")
    return report


def run_demo(path: Path, replay: bool = False) -> dict:
    initialize(path)
    catalog = read_model(path / "catalog.json", Catalog)
    outcomes = []
    with Ledger(path / "ledger.duckdb", catalog) as ledger:
        for index, file in enumerate(sorted(path.glob("bundle-*.json"))):
            bundle = read_model(file, Bundle)
            if replay and index == 0:
                from .extract import extract_bundle, replay_model

                extracted = extract_bundle(
                    bundle.source, catalog, model=replay_model(bundle.observations)
                )
                if extracted.ungrounded:
                    raise ValueError("Offline replay failed to align its fixture output")
                bundle = extracted.bundle
            outcomes.append(ledger.ingest(bundle))
        report = export_report(ledger, "2026-10", read_targets(str(path / "targets.csv")), path)
    return {
        "fictional_data": True,
        "report": str((path / "report.html").resolve()),
        "revision": report["revision"],
        "summary": report["summary"],
        "review_pending": report["review_pending_now"],
        "langextract_replay_sources": 1 if replay else 0,
        "model_inference_performed": False,
        "outcomes": outcomes,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Evidence-backed metric history for people and agents"
    )
    root.add_argument("--version", action="version", version="metric-thread 0.1.0")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Write fictional example inputs into a new workspace")
    init.add_argument("directory")
    demo = commands.add_parser("demo", help="Run the full fictional workflow without API keys")
    demo.add_argument("--output", default="workspace/demo")
    demo.add_argument(
        "--replay-extraction",
        action="store_true",
        help="Run LangExtract with known fixture model output",
    )
    schema = commands.add_parser("schema", help="Print a JSON input contract for an agent")
    schema.add_argument("kind", choices=["bundle", "catalog", "targets"])
    for name in ("ingest", "report", "history", "review", "status", "run", "dismiss"):
        command = commands.add_parser(name)
        command.add_argument("--catalog", required=True)
        command.add_argument("--db", required=True)
        if name == "ingest":
            command.add_argument("--bundle", required=True)
            command.add_argument("--expected-revision", type=int)
            command.add_argument("--fail-on-review", action="store_true")
        elif name == "report":
            command.add_argument("--period", required=True)
            command.add_argument("--targets")
            command.add_argument("--as-of", type=int)
            command.add_argument("--format", choices=["json", "html", "csv"], default="json")
            command.add_argument("--output")
        elif name == "run":
            command.add_argument("--inbox", required=True, help="Directory of reviewed bundles")
            command.add_argument("--pattern", default="*.json")
            command.add_argument("--period", required=True)
            command.add_argument("--targets")
            command.add_argument("--output", required=True)
            command.add_argument("--fail-on-review", action="store_true")
        elif name == "review":
            command.add_argument("--all", action="store_true", help="Include closed items")
        elif name == "dismiss":
            command.add_argument("--review-id", required=True)
            command.add_argument("--reason", required=True)
    extract = commands.add_parser(
        "extract", help="Propose a bundle using a configured LangExtract model"
    )
    extract.add_argument("--catalog", required=True)
    extract.add_argument("--source", required=True, help="UTF-8 plain text, already normalized")
    extract.add_argument("--source-id", required=True)
    extract.add_argument("--uri")
    extract.add_argument("--model", required=True, help="Explicit LangExtract provider/model ID")
    extract.add_argument("--output", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "schema":
            emit(
                {"bundle": Bundle, "catalog": Catalog, "targets": TargetSet}[
                    args.kind
                ].model_json_schema()
            )
            return 0
        if args.command == "init":
            initialize(Path(args.directory))
            emit({"workspace": args.directory, "fictional_inputs": True})
            return 0
        if args.command == "demo":
            emit(run_demo(Path(args.output), args.replay_extraction))
            return 0
        catalog = read_model(args.catalog, Catalog)
        if args.command == "extract":
            from .extract import extract_bundle

            source = Source(
                id=args.source_id, text=Path(args.source).read_text(encoding="utf-8"), uri=args.uri
            )
            result = extract_bundle(source, catalog, model_id=args.model)
            emit(result.bundle.model_dump(mode="json"), args.output)
            emit(
                {
                    "bundle": args.output,
                    "proposed": len(result.bundle.observations),
                    "ungrounded": result.ungrounded,
                    "next": "Review the bundle, then run ingest",
                }
            )
            return 0
        # Validate all batch input structures before accepting any facts.
        batch = None
        if args.command == "run":
            paths = sorted(Path(args.inbox).glob(args.pattern))
            if not paths:
                raise ValueError("No input bundles matched the inbox pattern")
            batch = [read_model(path, Bundle) for path in paths]
            batch_targets = read_targets(args.targets)
        with Ledger(args.db, catalog) as ledger:
            if args.command == "run":
                ledger.report(
                    args.period, batch_targets
                )  # Validate period and target semantics first.
                outcomes = [ledger.ingest(item) for item in batch]
                report = export_report(ledger, args.period, batch_targets, Path(args.output))
                emit(
                    {
                        "revision": report["revision"],
                        "summary": report["summary"],
                        "review_pending": report["review_pending_now"],
                        "outcomes": outcomes,
                        "report": str((Path(args.output) / "report.html").resolve()),
                    }
                )
                return 3 if args.fail_on_review and report["review_pending_now"] else 0
            if args.command == "ingest":
                result = ledger.ingest(read_model(args.bundle, Bundle), args.expected_revision)
                emit(result)
                return 3 if result["review"] and args.fail_on_review else 0
            if args.command == "report":
                result = ledger.report(args.period, read_targets(args.targets), args.as_of)
                rendered = {"json": lambda r: r, "html": render_html, "csv": render_csv}[
                    args.format
                ](result)
                emit(rendered, args.output)
            elif args.command == "history":
                emit(ledger.history())
            elif args.command == "review":
                emit(ledger.review(include_closed=args.all))
            elif args.command == "dismiss":
                ledger.dismiss(args.review_id, args.reason)
                emit({"dismissed": args.review_id, "reason": args.reason})
            elif args.command == "status":
                emit(
                    {
                        "revision": ledger.revision,
                        "catalog_sha256": catalog.fingerprint,
                        "review_pending": len(ledger.review()),
                    }
                )
        return 0
    except (ValueError, OSError) as exc:
        # ValidationError strings include the rejected input; return its field locations instead.
        if isinstance(exc, ValidationError):
            error = [{"field": list(e["loc"]), "message": e["msg"]} for e in exc.errors()]
        else:
            error = str(exc)
        print(json.dumps({"error": error}), file=sys.stderr)
        return 2
    except Exception as exc:
        # Avoid exposing provider responses or credentials through a generic traceback.
        print(
            json.dumps(
                {
                    "error": (
                        f"{type(exc).__name__}: operation failed; "
                        "check model or database configuration"
                    )
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
