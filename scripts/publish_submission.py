#!/usr/bin/env python3
"""Publish an experimental parser submitted through a GitHub issue.

The issue body is treated strictly as data. This script never executes contributor
content and only writes a declarative YAML parser after schema/semantic/privacy
validation succeeds.
"""
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
SCHEMA_PATH = ROOT / "schema" / "parser.schema.json"
PARSERS_DIR = ROOT / "parsers"
CATALOG_PATH = ROOT / "parser.json"
MARKER = "<!-- billy-parser-submission:v1 -->"
MAX_ISSUE_BODY_CHARS = 100_000
MAX_SUBMISSION_BYTES = 32_000

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


def _extract_yaml(body: str) -> str:
    if len(body) > MAX_ISSUE_BODY_CHARS:
        raise SubmissionError("Issue body is too large")
    if body.count(MARKER) != 1:
        raise SubmissionError("Missing or duplicate Billy submission marker")
    after = body.split(MARKER, 1)[1]
    match = re.search(r"```(?:yaml|yml)\s*\n(?P<content>.*?)\n```", after, re.IGNORECASE | re.DOTALL)
    if match is None:
        raise SubmissionError("Submission must contain one fenced YAML block after the Billy marker")
    content = match.group("content").strip() + "\n"
    if len(content.encode("utf-8")) > MAX_SUBMISSION_BYTES:
        raise SubmissionError("Parser YAML exceeds the 32 KB community submission limit")
    return content


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
        raise SubmissionError("Bot accounts cannot own experimental parsers")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", author):
        raise SubmissionError("Invalid GitHub author login")
    return author


def _target_path(root: Path, parser: dict[str, Any]) -> Path:
    parser_id = str(parser.get("id") or "")
    metadata = parser.get("metadata") or {}
    country = str(metadata.get("country") or "").casefold()
    bill_type = str(metadata.get("bill_type") or "").casefold()
    parts = parser_id.split(".")
    if len(parts) < 3:
        raise SubmissionError("Community parser IDs must contain country, provider and bill type")
    if parts[0] != country:
        raise SubmissionError("Parser ID country prefix does not match metadata.country")
    if parts[-1] != bill_type:
        raise SubmissionError("Parser ID suffix must match metadata.bill_type")
    # parser id validation disallows slashes and traversal characters. The path is
    # nevertheless constructed from individual id segments instead of user paths.
    return root / "parsers" / Path(*parts[:-1]) / f"{parts[-1]}.yaml"


def _existing_parsers(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    result: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted((root / "parsers").rglob("*.yaml")):
        data = _BUILD._load_yaml(path)
        parser_id = str(data.get("id") or "")
        if parser_id:
            result[parser_id] = (path, data)
    return result


def _validate_submission(root: Path, parser: dict[str, Any], author: str) -> tuple[Path, bool]:
    metadata = parser.get("metadata")
    if not isinstance(metadata, dict):
        raise SubmissionError("metadata is required")

    # Community submissions can never self-promote. Ownership is injected from
    # github.event.issue.user.login, never trusted from the submitted YAML.
    metadata["quality"] = "experimental"
    metadata["submitted_by"] = author

    schema = json.loads((root / "schema" / "parser.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(parser), key=lambda err: list(err.absolute_path))
    if errors:
        lines = []
        for error in errors[:12]:
            location = ".".join(str(x) for x in error.absolute_path) or "<root>"
            lines.append(f"{location}: {error.message}")
        raise SubmissionError("Schema validation failed: " + "; ".join(lines))

    parser_id = str(parser["id"])
    existing = _existing_parsers(root)
    target = _target_path(root, parser)
    is_update = parser_id in existing

    if is_update:
        existing_path, old = existing[parser_id]
        old_meta = old.get("metadata") or {}
        if str(old_meta.get("quality") or "") != "experimental":
            raise SubmissionError("Only experimental community parsers can be updated through this channel")
        owner = str(old_meta.get("submitted_by") or "")
        if owner.casefold() != author.casefold():
            raise SubmissionError(f"Parser {parser_id} is owned by @{owner or 'unknown'}")
        old_version = int(old.get("version") or 0)
        new_version = int(parser.get("version") or 0)
        if new_version <= old_version:
            raise SubmissionError(f"Parser version must increase above v{old_version}")
        for key in ("country", "provider", "bill_type"):
            if str(metadata.get(key) or "").casefold() != str(old_meta.get(key) or "").casefold():
                raise SubmissionError(f"metadata.{key} cannot change on an automatic experimental update")
        # Preserve the original repository path even if a future path convention changes.
        target = existing_path
    else:
        if target.exists():
            other = _BUILD._load_yaml(target)
            raise SubmissionError(
                f"Target path is already used by parser {other.get('id') or 'unknown'}"
            )

    # Semantic/privacy/regex validation. Temporarily point build_catalog's ROOT at
    # this repository root so its path-aware diagnostics stay correct in tests.
    old_root = _BUILD.ROOT
    try:
        _BUILD.ROOT = root
        try:
            _BUILD._semantic_validate(parser, target)
        except ValueError as err:
            raise SubmissionError(str(err)) from err
    finally:
        _BUILD.ROOT = old_root

    return target, is_update


def _canonical_yaml(parser: dict[str, Any]) -> str:
    return yaml.safe_dump(
        parser,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    )


def publish_submission(root: Path, author: str, body: str) -> dict[str, Any]:
    root = Path(root)
    author = _validate_author(author)
    content = _extract_yaml(body)
    parser = _load_parser(content)
    target, is_update = _validate_submission(root, parser, author)

    target.parent.mkdir(parents=True, exist_ok=True)
    canonical = _canonical_yaml(parser)
    target.write_text(canonical, encoding="utf-8")

    try:
        catalog = _BUILD.build_catalog(root)
    except Exception as err:
        # The workflow workspace is disposable, but removing a newly written file
        # makes local/test use less surprising. Updates are restored by the caller's
        # git checkout, so we leave them in place only in the ephemeral workflow.
        raise SubmissionError(f"Catalog validation failed: {err}") from err

    # Catalog generation is intentionally a second workflow phase. The parser is
    # committed first, then parser.json is generated with source_commit pinned to
    # that immutable parser commit SHA.
    return {
        "parser_id": str(parser["id"]),
        "version": int(parser["version"]),
        "path": target.relative_to(root).as_posix(),
        "author": author,
        "update": is_update,
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

    message = (
        f"Published {result['parser_id']} v{result['version']} as experimental "
        f"for @{result['author']}."
    )
    _write_result(args.result, ok=True, message=message, issue_number=issue_number, **result)
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
