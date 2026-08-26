#!/usr/bin/env python3
"""Process anonymous Billy parser feedback and apply automatic promotion."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- billy-parser-feedback:v1 -->"
MAX_ISSUE_BODY_CHARS = 32_000
MAX_JSON_BYTES = 8_000

_BUILD_SPEC = importlib.util.spec_from_file_location("build_catalog", ROOT / "scripts" / "build_catalog.py")
_BUILD = importlib.util.module_from_spec(_BUILD_SPEC)
assert _BUILD_SPEC.loader is not None
_BUILD_SPEC.loader.exec_module(_BUILD)

_POLICY_SPEC = importlib.util.spec_from_file_location(
    "community_policy", ROOT / "scripts" / "community_policy.py"
)
_POLICY = importlib.util.module_from_spec(_POLICY_SPEC)
assert _POLICY_SPEC.loader is not None
_POLICY_SPEC.loader.exec_module(_POLICY)


class FeedbackError(ValueError):
    """The feedback issue cannot be processed automatically."""


def _write_result(result_path: Path | None, *, ok: bool, message: str, **extra: Any) -> None:
    if result_path is None:
        return
    payload = {"ok": ok, "message": message, **extra}
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _extract_json(body: str) -> dict[str, Any]:
    if len(body) > MAX_ISSUE_BODY_CHARS:
        raise FeedbackError("Issue body is too large")
    if body.count(MARKER) != 1:
        raise FeedbackError("Missing or duplicate Billy feedback marker")
    after = body.split(MARKER, 1)[1]
    fenced = re.search(r"```json\s*\n(?P<content>.*?)\n```", after, re.IGNORECASE | re.DOTALL)
    if fenced is not None:
        raw = fenced.group("content").strip()
        if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
            raise FeedbackError("Feedback JSON exceeds 8 KB")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as err:
            raise FeedbackError(f"Invalid feedback JSON: {err}") from err
    else:
        candidate = after.lstrip()
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError as err:
            raise FeedbackError(f"Invalid feedback JSON: {err}") from err
    if not isinstance(value, dict):
        raise FeedbackError("Feedback JSON root must be an object")
    return value


def _validate_schema(root: Path, payload: dict[str, Any]) -> None:
    schema = json.loads((root / "schema" / "feedback.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda err: list(err.absolute_path))
    if errors:
        message = "; ".join(
            f"{'.'.join(str(x) for x in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors[:12]
        )
        raise FeedbackError(f"Feedback JSON schema validation failed: {message}")


def _existing_parser(root: Path, parser_id: str) -> tuple[Path, dict[str, Any]]:
    for path in sorted((root / "parsers").rglob("*.yaml")):
        parser = _BUILD._load_yaml(path)
        if str(parser.get("id") or "") == parser_id:
            return path, parser
    raise FeedbackError(f"Parser {parser_id} does not exist")


def _feedback_path(root: Path, parser_id: str, version: int) -> Path:
    parts = parser_id.split(".")
    if len(parts) < 3 or any(not re.fullmatch(r"[a-z0-9_-]+", part) for part in parts):
        raise FeedbackError("Unsafe parser id")
    target = root / "feedback" / Path(*parts[:-1]) / parts[-1] / f"v{version}.json"
    feedback_root = (root / "feedback").resolve()
    if feedback_root not in target.resolve().parents:
        raise FeedbackError("Unsafe feedback path")
    return target


def _load_store(path: Path, parser_id: str, version: int) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "parser_id": parser_id,
            "version": version,
            "votes": {},
            "updated_at": "",
            "community_verified": False,
        }
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise FeedbackError(f"Corrupt feedback store: {err}") from err
    if not isinstance(store, dict) or not isinstance(store.get("votes"), dict):
        raise FeedbackError("Corrupt feedback store")
    if store.get("parser_id") != parser_id or store.get("version") != version:
        raise FeedbackError("Feedback store identity mismatch")
    votes = store["votes"]
    for fingerprint, result in votes.items():
        if not re.fullmatch(r"[a-f0-9]{64}", str(fingerprint)):
            raise FeedbackError("Feedback store contains an invalid fingerprint")
        if result not in {"working", "partial", "failed"}:
            raise FeedbackError("Feedback store contains an invalid result")
    return store


def _counts(votes: dict[str, str]) -> dict[str, int]:
    working = sum(1 for value in votes.values() if value == "working")
    partial = sum(1 for value in votes.values() if value == "partial")
    failed = sum(1 for value in votes.values() if value == "failed")
    return {
        "working": working,
        "partial": partial,
        "failed": failed,
        "contributors": len(votes),
    }


def _canonical_yaml(parser: dict[str, Any]) -> str:
    return yaml.safe_dump(parser, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)


def process_feedback(root: Path, body: str, *, now: str | None = None) -> dict[str, Any]:
    root = Path(root)
    payload = _extract_json(body)
    _validate_schema(root, payload)

    parser_id = str(payload["parser_id"])
    version = int(payload["version"])
    fingerprint = str(payload["installation_fingerprint"])
    result = str(payload["result"])

    parser_path, parser = _existing_parser(root, parser_id)
    current_version = int(parser.get("version") or 0)
    if current_version != version:
        raise FeedbackError(
            f"Feedback version v{version} is not the current version of {parser_id} (v{current_version})"
        )

    store_path = _feedback_path(root, parser_id, version)
    store = _load_store(store_path, parser_id, version)
    votes: dict[str, str] = store["votes"]
    previous = votes.get(fingerprint)
    changed = previous != result
    if changed:
        votes[fingerprint] = result
        store["updated_at"] = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    counts = _counts(votes)
    metadata = parser.get("metadata") or {}
    status_before = _BUILD._catalog_status(metadata)
    promoted = False
    demoted = False
    meets_threshold = _POLICY.should_promote(
        working=counts["working"], partial=counts["partial"], failed=counts["failed"]
    )
    community_verified = bool(store.get("community_verified"))
    if status_before == "experimental" and meets_threshold:
        metadata["status"] = "verified"
        metadata["quality"] = "verified"
        parser["metadata"] = metadata
        parser_path.write_text(_canonical_yaml(parser), encoding="utf-8")
        promoted = True
        community_verified = True
    elif status_before == "verified" and community_verified and not meets_threshold:
        metadata["status"] = "experimental"
        metadata["quality"] = "experimental"
        parser["metadata"] = metadata
        parser_path.write_text(_canonical_yaml(parser), encoding="utf-8")
        demoted = True
        community_verified = False

    if store.get("community_verified") != community_verified:
        store["community_verified"] = community_verified
        store["updated_at"] = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    try:
        _BUILD.build_catalog(root)
    except Exception as err:
        raise FeedbackError(f"Catalog validation failed: {err}") from err

    return {
        "parser_id": parser_id,
        "version": version,
        "result": result,
        "changed": changed,
        "previous_result": previous,
        "feedback_path": store_path.relative_to(root).as_posix(),
        "parser_path": parser_path.relative_to(root).as_posix(),
        "promoted": promoted,
        "demoted": demoted,
        "status": "verified" if promoted else "experimental" if demoted else status_before,
        "feedback": counts,
    }


def _event_payload(path: Path) -> tuple[str, int]:
    event = json.loads(path.read_text(encoding="utf-8"))
    issue = event.get("issue") or {}
    body = str(issue.get("body") or "")
    number = int(issue.get("number") or 0)
    if not number:
        raise FeedbackError("GitHub issue event is missing issue number")
    return body, number


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=Path, required=True, help="GitHub event JSON path")
    parser.add_argument("--result", type=Path, help="Write machine-readable result JSON")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()

    try:
        body, issue_number = _event_payload(args.event)
        result = process_feedback(args.root, body)
    except Exception as err:
        message = str(err)
        _write_result(args.result, ok=False, message=message)
        print(message, file=sys.stderr)
        return 1

    promotion = " Promoted to verified." if result["promoted"] else ""
    changed = "updated" if result["changed"] else "unchanged"
    message = (
        f"Accepted {result['result']} feedback for {result['parser_id']} v{result['version']} "
        f"({changed}).{promotion}"
    )
    _write_result(args.result, ok=True, message=message, issue_number=issue_number, **result)
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
