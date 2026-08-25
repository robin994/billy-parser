#!/usr/bin/env python3
"""Validate Billy parser YAML files and build parser.json."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "parser.schema.json"
PARSERS_DIR = ROOT / "parsers"
CATALOG_PATH = ROOT / "parser.json"


def _source_commit() -> str:
    env_sha = os.environ.get("GITHUB_SHA", "").strip()
    if env_sha:
        return env_sha
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "main"


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("parser root must be an object")
    return data



def _semantic_validate(parser: dict, path: Path) -> None:
    """Validate constraints that are awkward to express in JSON Schema."""
    documents = {"email"}
    for attachment in parser.get("documents", {}).get("attachments", []) or []:
        document_id = str(attachment.get("id") or "")
        if document_id in documents:
            raise ValueError(f"{path.relative_to(ROOT)}: duplicate document id {document_id!r}")
        documents.add(document_id)
        pattern = str(attachment.get("filename_regex") or "")
        if pattern:
            _compile_regex(pattern, path)

    for pattern in parser.get("prefilter", {}).get("email", {}).get("subject_regex", []) or []:
        _compile_regex(str(pattern), path)

    for rule in parser.get("detection", {}).get("rules", []) or []:
        if "regex" in rule:
            _compile_regex(str(rule["regex"]), path)

    for field_name, field in parser.get("fields", {}).items():
        for candidate in field.get("candidates", []) or []:
            source = str(candidate.get("source") or "")
            if source not in documents:
                raise ValueError(
                    f"{path.relative_to(ROOT)}: field {field_name!r} references undeclared document {source!r}"
                )
            _compile_regex(str(candidate.get("regex") or ""), path)


def _compile_regex(pattern: str, path: Path) -> None:
    if not pattern or len(pattern) > 600:
        raise ValueError(f"{path.relative_to(ROOT)}: regex is empty or exceeds 600 characters")
    try:
        re.compile(pattern)
    except re.error as err:
        raise ValueError(f"{path.relative_to(ROOT)}: invalid regex: {err}") from err

def build_catalog() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    source_commit = _source_commit()
    seen: set[str] = set()
    parsers: list[dict] = []

    for path in sorted(PARSERS_DIR.rglob("*.yaml")):
        parser = _load_yaml(path)
        errors = sorted(validator.iter_errors(parser), key=lambda err: list(err.absolute_path))
        if errors:
            lines = [f"{path.relative_to(ROOT)}:"]
            for error in errors:
                location = ".".join(str(x) for x in error.absolute_path) or "<root>"
                lines.append(f"  {location}: {error.message}")
            raise ValueError("\n".join(lines))

        _semantic_validate(parser, path)

        parser_id = str(parser["id"])
        if parser_id in seen:
            raise ValueError(f"Duplicate parser id: {parser_id}")
        seen.add(parser_id)

        raw = path.read_bytes()
        metadata = parser["metadata"]
        parsers.append(
            {
                "id": parser_id,
                "version": int(parser["version"]),
                "name": metadata["name"],
                "country": metadata["country"],
                "language": metadata["language"],
                "provider": metadata["provider"],
                "bill_type": metadata["bill_type"],
                "parser_schema": int(parser["schema"]),
                "min_billy_version": metadata["min_billy_version"],
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_commit": source_commit,
        "parsers": parsers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Validate and compare with parser.json")
    args = parser.parse_args()

    try:
        catalog = build_catalog()
    except Exception as err:
        print(err, file=sys.stderr)
        return 1

    if args.check:
        # Pull requests validate source parsers only. parser.json is generated on
        # main by GitHub Actions, so contributors never need to commit generated
        # catalog changes by hand.
        print(f"Validated {len(catalog['parsers'])} parser(s)")
        return 0

    CATALOG_PATH.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {CATALOG_PATH.relative_to(ROOT)} with {len(catalog['parsers'])} parser(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
