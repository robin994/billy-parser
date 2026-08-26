#!/usr/bin/env python3
"""Validate Billy parser YAML files and build legacy + sharded catalogs."""
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
CATALOG_DIR = ROOT / "catalog"

SENSITIVE_STATIC_FIELDS = {
    "invoice_number",
    "customer_code",
    "pod",
    "pdr",
    "tax_code",
    "fiscal_code",
    "iban",
    "account_number",
    "contract_number",
    "phone",
    "email",
    "address",
}


def _source_commit() -> str:
    override = os.environ.get("BILLY_SOURCE_COMMIT", "").strip()
    if override:
        return override
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


def _regex_safety_validate(pattern: str, path: Path) -> None:
    """Reject a small set of regex constructs that are risky in community parsers.

    This is deliberately conservative and supplements Billy's runtime limits. It is
    not intended to prove a regex is linear-time, only to block common catastrophic
    patterns and backreferences before an experimental parser reaches the catalog.
    """
    # Numeric / named backreferences can make matching far more expensive and are
    # unnecessary for Billy's extraction model.
    if re.search(r"(?<!\\)\\[1-9]", pattern) or "(?P=" in pattern:
        raise ValueError(f"{path.relative_to(ROOT)}: regex backreferences are not allowed")
    if "(?(" in pattern:
        raise ValueError(f"{path.relative_to(ROOT)}: conditional regex groups are not allowed")

    # Catch the most common nested-quantifier forms: (.+)+, (.*)*, (\w+)+, etc.
    if re.search(r"\((?:\?:)?(?:[^()\\]|\\.){0,180}[+*](?:[^()\\]|\\.){0,180}\)[+*]", pattern):
        raise ValueError(f"{path.relative_to(ROOT)}: nested regex quantifiers are not allowed")


def _compile_regex(pattern: str, path: Path) -> None:
    if not pattern or len(pattern) > 600:
        raise ValueError(f"{path.relative_to(ROOT)}: regex is empty or exceeds 600 characters")
    _regex_safety_validate(pattern, path)
    try:
        re.compile(pattern)
    except re.error as err:
        raise ValueError(f"{path.relative_to(ROOT)}: invalid regex: {err}") from err


def _semantic_validate(parser: dict, path: Path) -> None:
    """Validate constraints that are awkward to express in JSON Schema."""
    metadata = parser.get("metadata", {})
    parser_id = str(parser.get("id") or "")
    country = str(metadata.get("country") or "")
    if parser_id and country and parser_id.split(".", 1)[0] != country.casefold():
        raise ValueError(
            f"{path.relative_to(ROOT)}: parser id must start with the lowercase country code"
        )
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
        if field_name in SENSITIVE_STATIC_FIELDS and "value" in field:
            raise ValueError(
                f"{path.relative_to(ROOT)}: sensitive field {field_name!r} cannot use a static value"
            )
        for candidate in field.get("candidates", []) or []:
            source = str(candidate.get("source") or "")
            if source not in documents:
                raise ValueError(
                    f"{path.relative_to(ROOT)}: field {field_name!r} references undeclared document {source!r}"
                )
            _compile_regex(str(candidate.get("regex") or ""), path)


def _catalog_status(metadata: dict) -> str:
    """Return the parser lifecycle state exposed by parser.json.

    status is the source of truth. The fallbacks keep old parser definitions readable
    while repositories migrate away from quality/deprecated as lifecycle markers.
    """
    status = str(metadata.get("status") or "").strip().casefold()
    if status in {"experimental", "verified", "outdated"}:
        return status
    if bool(metadata.get("deprecated")):
        return "outdated"
    if str(metadata.get("quality") or "") == "experimental":
        return "experimental"
    return "verified"


def build_catalog(root: Path | None = None) -> dict:
    root = Path(root or ROOT)
    schema_path = root / "schema" / "parser.schema.json"
    parsers_dir = root / "parsers"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    source_commit = _source_commit() if root == ROOT else "main"
    seen: set[str] = set()
    parsers: list[dict] = []

    for path in sorted(parsers_dir.rglob("*.yaml")):
        parser = _load_yaml(path)
        errors = sorted(validator.iter_errors(parser), key=lambda err: list(err.absolute_path))
        if errors:
            lines = [f"{path.relative_to(root)}:"]
            for error in errors:
                location = ".".join(str(x) for x in error.absolute_path) or "<root>"
                lines.append(f"  {location}: {error.message}")
            raise ValueError("\n".join(lines))

        # The semantic validator reports paths relative to the module ROOT. For
        # tests using a temporary root, validate against the real path through a
        # lightweight shim if necessary.
        if root == ROOT:
            _semantic_validate(parser, path)
        else:
            _semantic_validate_for_root(parser, path, root)

        parser_id = str(parser["id"])
        if parser_id in seen:
            raise ValueError(f"Duplicate parser id: {parser_id}")
        seen.add(parser_id)

        raw = path.read_bytes()
        metadata = parser["metadata"]
        row = {
            "id": parser_id,
            "version": int(parser["version"]),
            "name": metadata["name"],
            "country": metadata["country"],
            "language": metadata["language"],
            "provider": metadata["provider"],
            "bill_type": metadata["bill_type"],
            "quality": metadata.get(
                "quality", "experimental" if _catalog_status(metadata) == "experimental" else "verified"
            ),
            "status": _catalog_status(metadata),
            "parser_schema": int(parser["schema"]),
            "min_billy_version": metadata["min_billy_version"],
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }
        for optional in ("submitted_by", "changelog", "deprecated", "replacement"):
            if optional in metadata:
                row[optional] = metadata[optional]
        parsers.append(row)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_commit": source_commit,
        "parsers": parsers,
    }


def build_catalog_v2(catalog: dict) -> tuple[dict, dict[str, dict]]:
    """Split one validated catalog into a small index and country shards."""
    generated_at = str(catalog["generated_at"])
    source_commit = str(catalog["source_commit"])
    grouped: dict[str, list[dict]] = {}

    for parser in catalog["parsers"]:
        country = str(parser["country"]).upper()
        grouped.setdefault(country, []).append(parser)

    shards: dict[str, dict] = {}
    countries: dict[str, dict] = {}
    for country in sorted(grouped):
        parsers = sorted(grouped[country], key=lambda item: str(item["id"]))
        shards[country] = {
            "schema_version": 2,
            "country": country,
            "generated_at": generated_at,
            "source_commit": source_commit,
            "parsers": parsers,
        }
        countries[country] = {
            "path": f"catalog/{country.casefold()}.json",
            "parsers": len(parsers),
        }

    index = {
        "schema_version": 2,
        "generated_at": generated_at,
        "source_commit": source_commit,
        "countries": countries,
    }
    return index, shards


def write_catalogs(catalog: dict, root: Path | None = None) -> None:
    """Write Catalog v2 plus parser.json for legacy Billy clients."""
    root = Path(root or ROOT)
    catalog_path = root / "parser.json"
    catalog_dir = root / "catalog"
    index, shards = build_catalog_v2(catalog)

    catalog_path.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    catalog_dir.mkdir(parents=True, exist_ok=True)

    expected = {f"{country.casefold()}.json" for country in shards}
    for stale in catalog_dir.glob("*.json"):
        if stale.name != "index.json" and stale.name not in expected:
            stale.unlink()

    for country, shard in shards.items():
        (catalog_dir / f"{country.casefold()}.json").write_text(
            json.dumps(shard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    (catalog_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _semantic_validate_for_root(parser: dict, path: Path, root: Path) -> None:
    """Temporary-root variant used by publisher tests."""
    global ROOT
    old_root = ROOT
    try:
        ROOT = root
        _semantic_validate(parser, path)
    finally:
        ROOT = old_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Validate source parsers")
    args = parser.parse_args()

    try:
        catalog = build_catalog()
    except Exception as err:
        print(err, file=sys.stderr)
        return 1

    if args.check:
        # Pull requests validate source parsers only. Generated catalogs are built
        # on main by GitHub Actions, so contributors do not edit them by hand.
        print(f"Validated {len(catalog['parsers'])} parser(s)")
        return 0

    write_catalogs(catalog)
    _, shards = build_catalog_v2(catalog)
    print(
        f"Wrote {CATALOG_PATH.relative_to(ROOT)} and {len(shards)} country shard(s) "
        f"with {len(catalog['parsers'])} parser(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
