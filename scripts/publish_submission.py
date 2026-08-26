#!/usr/bin/env python3
"""Validate and publish a Billy parser submission v2 from a GitHub issue."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- billy-parser-submission:v2 -->"
MAX_ISSUE_BODY_CHARS = 100_000
MAX_SUBMISSION_BYTES = 32_000
MAX_JSON_BYTES = 8_000

_SPEC = importlib.util.spec_from_file_location("build_catalog", ROOT / "scripts" / "build_catalog.py")
_BUILD = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_BUILD)


class SubmissionError(ValueError):
    """The community submission cannot be published automatically."""


def _write_result(result_path: Path | None, *, ok: bool, message: str, **extra: Any) -> None:
    if result_path is None:
        return
    payload = {"ok": ok, "message": message, **extra}
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _extract_json(after_marker: str) -> dict[str, Any]:
    fenced = re.search(r"```json\s*\n(?P<content>.*?)\n```", after_marker, re.IGNORECASE | re.DOTALL)
    if fenced is not None:
        raw = fenced.group("content").strip()
    else:
        candidate = after_marker.lstrip()
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError as err:
            raise SubmissionError(f"Invalid submission JSON: {err}") from err
        if not isinstance(value, dict):
            raise SubmissionError("Submission JSON root must be an object")
        return value

    if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
        raise SubmissionError("Submission JSON exceeds 8 KB")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as err:
        raise SubmissionError(f"Invalid submission JSON: {err}") from err
    if not isinstance(value, dict):
        raise SubmissionError("Submission JSON root must be an object")
    return value


def _extract_payloads(body: str) -> tuple[dict[str, Any], str]:
    if len(body) > MAX_ISSUE_BODY_CHARS:
        raise SubmissionError("Issue body is too large")
    if body.count(MARKER) != 1:
        raise SubmissionError("Missing or duplicate Billy submission v2 marker")
    after = body.split(MARKER, 1)[1]
    envelope = _extract_json(after)
    yaml_match = re.search(r"```(?:yaml|yml)\s*\n(?P<content>.*?)\n```", after, re.IGNORECASE | re.DOTALL)
    if yaml_match is None:
        raise SubmissionError("Submission must contain one fenced YAML block after the marker")
    content = yaml_match.group("content").strip() + "\n"
    if len(content.encode("utf-8")) > MAX_SUBMISSION_BYTES:
        raise SubmissionError("Parser YAML exceeds the 32 KB community submission limit")
    return envelope, content


def _load_parser(content: str) -> dict[str, Any]:
    try:
        parser = yaml.safe_load(content)
    except yaml.YAMLError as err:
        raise SubmissionError(f"Invalid YAML: {err}") from err
    if not isinstance(parser, dict):
        raise SubmissionError("Parser root must be an object")
    return parser


def _validate_author(author: str) -> str:
    author = author.strip()
    if author.endswith("[bot]"):
        raise SubmissionError("Bot accounts cannot submit community parsers")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", author):
        raise SubmissionError("Invalid GitHub author login")
    return author


def _validate_json_schema(root: Path, envelope: dict[str, Any]) -> None:
    schema = json.loads((root / "schema" / "submission.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(envelope), key=lambda err: list(err.absolute_path))
    if errors:
        message = "; ".join(
            f"{'.'.join(str(x) for x in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors[:12]
        )
        raise SubmissionError(f"Submission JSON schema validation failed: {message}")


def _validate_parser_schema(root: Path, parser: dict[str, Any]) -> None:
    schema = json.loads((root / "schema" / "parser.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(parser), key=lambda err: list(err.absolute_path))
    if errors:
        message = "; ".join(
            f"{'.'.join(str(x) for x in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors[:12]
        )
        raise SubmissionError(f"Parser schema validation failed: {message}")


def _target_path(root: Path, parser: dict[str, Any]) -> Path:
    parser_id = str(parser.get("id") or "")
    metadata = parser.get("metadata") or {}
    country = str(metadata.get("country") or "").casefold()
    bill_type = str(metadata.get("bill_type") or "").casefold()
    parts = parser_id.split(".")
    if len(parts) < 3:
        raise SubmissionError("Parser IDs must contain country, provider and bill type")
    if parts[0] != country:
        raise SubmissionError("Parser ID country prefix does not match metadata.country")
    if parts[-1] != bill_type:
        raise SubmissionError("Parser ID suffix must match metadata.bill_type")
    if any(not re.fullmatch(r"[a-z0-9_-]+", part) for part in parts):
        raise SubmissionError("Parser ID contains an unsafe path segment")
    target = root / "parsers" / Path(*parts[:-1]) / f"{parts[-1]}.yaml"
    resolved_root = (root / "parsers").resolve()
    if resolved_root not in target.resolve().parents:
        raise SubmissionError("Unsafe parser path")
    return target


def _existing_parsers(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    result: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted((root / "parsers").rglob("*.yaml")):
        data = _BUILD._load_yaml(path)
        parser_id = str(data.get("id") or "")
        if parser_id:
            result[parser_id] = (path, data)
    return result


def _canonical_yaml(parser: dict[str, Any]) -> str:
    return yaml.safe_dump(parser, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)


def _validate_contract(envelope: dict[str, Any], parser: dict[str, Any]) -> None:
    metadata = parser.get("metadata") or {}
    checks = {
        "parser_id": parser.get("id"),
        "version": parser.get("version"),
        "country": metadata.get("country"),
        "provider": metadata.get("provider"),
        "bill_type": metadata.get("bill_type"),
    }
    for key, actual in checks.items():
        if envelope.get(key) != actual:
            raise SubmissionError(f"Submission JSON {key} does not match parser YAML")


def publish_submission(root: Path, author: str, body: str) -> dict[str, Any]:
    root = Path(root)
    author = _validate_author(author)
    envelope, content = _extract_payloads(body)
    _validate_json_schema(root, envelope)
    parser = _load_parser(content)
    _validate_contract(envelope, parser)

    metadata = parser.get("metadata")
    if not isinstance(metadata, dict):
        raise SubmissionError("metadata is required")
    metadata["status"] = "experimental"
    metadata["quality"] = "experimental"
    metadata["submitted_by"] = author

    _validate_parser_schema(root, parser)
    target = _target_path(root, parser)

    old_root = _BUILD.ROOT
    try:
        _BUILD.ROOT = root
        _BUILD._semantic_validate(parser, target)
    except ValueError as err:
        raise SubmissionError(str(err)) from err
    finally:
        _BUILD.ROOT = old_root

    parser_id = str(parser["id"])
    version = int(parser["version"])
    existing = _existing_parsers(root)
    is_update = parser_id in existing
    already_processed = False

    if is_update:
        existing_path, old = existing[parser_id]
        old_version = int(old.get("version") or 0)
        target = existing_path
        if version < old_version:
            raise SubmissionError(f"Parser version must be greater than current v{old_version}")
        if version == old_version:
            normalized_old = dict(old)
            old_meta = dict(normalized_old.get("metadata") or {})
            old_meta["status"] = "experimental"
            old_meta["quality"] = "experimental"
            old_meta["submitted_by"] = author
            normalized_old["metadata"] = old_meta
            if _canonical_yaml(normalized_old) == _canonical_yaml(parser):
                already_processed = True
            else:
                raise SubmissionError(f"Parser version must increase above v{old_version}")

    if not already_processed:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_canonical_yaml(parser), encoding="utf-8")

    try:
        _BUILD.build_catalog(root)
    except Exception as err:
        raise SubmissionError(f"Catalog validation failed: {err}") from err

    return {
        "parser_id": parser_id,
        "version": version,
        "path": target.relative_to(root).as_posix(),
        "author": author,
        "update": is_update,
        "already_processed": already_processed,
        "quality": "experimental",
        "status": "experimental",
    }


def _event_payload(path: Path) -> tuple[str, str, int]:
    event = json.loads(path.read_text(encoding="utf-8"))
    issue = event.get("issue") or {}
    user = issue.get("user") or {}
    author = str(user.get("login") or "")
    body = str(issue.get("body") or "")
    number = int(issue.get("number") or 0)
    if not author or not number:
        raise SubmissionError("GitHub issue event is missing author or issue number")
    return author, body, number


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=Path, required=True, help="GitHub event JSON path")
    parser.add_argument("--result", type=Path, help="Write machine-readable result JSON")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()

    try:
        author, body, issue_number = _event_payload(args.event)
        result = publish_submission(args.root, author, body)
    except Exception as err:
        message = str(err)
        _write_result(args.result, ok=False, message=message)
        print(message, file=sys.stderr)
        return 1

    action = "Already processed" if result["already_processed"] else "Accepted"
    message = f"{action}: {result['parser_id']} v{result['version']} is experimental."
    _write_result(args.result, ok=True, message=message, issue_number=issue_number, **result)
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
