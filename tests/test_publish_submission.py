from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "publish_submission", ROOT / "scripts" / "publish_submission.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "schema", root / "schema")
    shutil.copytree(ROOT / "parsers", root / "parsers")
    return root


def _parser(version: int = 1) -> dict:
    return {
        "schema": 1,
        "id": "it.community.internet",
        "version": version,
        "metadata": {
            "name": "Community ISP",
            "country": "IT",
            "language": "it",
            "provider": "Community ISP",
            "bill_type": "internet",
            "min_billy_version": "0.11.3",
        },
        "prefilter": {"email": {"from": ["billing@example.invalid"]}},
        "detection": {
            "threshold": 50,
            "rules": [
                {
                    "source": "email.from",
                    "equals": "billing@example.invalid",
                    "weight": 50,
                }
            ],
        },
        "documents": {"email": {"enabled": True}},
        "fields": {
            "provider": {"value": "Community ISP"},
            "bill_type": {"value": "internet"},
            "currency": {"value": "EUR"},
            "amount": {
                "required": True,
                "candidates": [
                    {"source": "email", "regex": r"Totale\s+(?P<value>[0-9.,]+)"}
                ],
                "transform": {"type": "decimal", "locale": "it_IT"},
            },
        },
    }


def _envelope(parser: dict) -> dict:
    meta = parser["metadata"]
    return {
        "schema_version": 2,
        "parser_id": parser["id"],
        "version": parser["version"],
        "country": meta["country"],
        "provider": meta["provider"],
        "bill_type": meta["bill_type"],
        "requested_status": "experimental",
        "billy_version": "0.11.3",
    }


def _body(parser: dict, envelope: dict | None = None) -> str:
    envelope = envelope or _envelope(parser)
    content = yaml.safe_dump(parser, sort_keys=False, allow_unicode=True)
    return (
        f"{MODULE.MARKER}\n\n"
        f"```json\n{json.dumps(envelope)}\n```\n\n"
        f"```yaml\n{content}```\n"
    )


def test_new_submission_is_forced_experimental_and_catalog_valid(tmp_path: Path):
    root = _repo(tmp_path)
    parser = _parser()
    parser["metadata"].update({"status": "verified", "quality": "verified"})

    result = MODULE.publish_submission(root, "alice", _body(parser))

    saved = yaml.safe_load((root / result["path"]).read_text())
    assert result["update"] is False
    assert saved["metadata"]["status"] == "experimental"
    assert saved["metadata"]["quality"] == "experimental"
    assert saved["metadata"]["submitted_by"] == "alice"
    item = next(
        item for item in MODULE._BUILD.build_catalog(root)["parsers"] if item["id"] == parser["id"]
    )
    assert item["status"] == "experimental"


def test_invalid_json_is_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    body = f"{MODULE.MARKER}\n```json\n{{bad\n```\n```yaml\nfoo: bar\n```"
    with pytest.raises(MODULE.SubmissionError, match="Invalid submission JSON"):
        MODULE.publish_submission(root, "alice", body)


def test_invalid_yaml_is_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    parser = _parser()
    body = (
        f"{MODULE.MARKER}\n```json\n{json.dumps(_envelope(parser))}\n```\n"
        "```yaml\n[broken\n```"
    )
    with pytest.raises(MODULE.SubmissionError, match="Invalid YAML"):
        MODULE.publish_submission(root, "alice", body)


def test_contract_parser_id_must_match_yaml(tmp_path: Path):
    root = _repo(tmp_path)
    parser = _parser()
    envelope = _envelope(parser)
    envelope["parser_id"] = "it.other.internet"
    with pytest.raises(MODULE.SubmissionError, match="parser_id does not match"):
        MODULE.publish_submission(root, "alice", _body(parser, envelope))


def test_country_must_be_valid_and_coherent(tmp_path: Path):
    root = _repo(tmp_path)
    parser = _parser()
    envelope = _envelope(parser)
    envelope["country"] = "Italy"
    with pytest.raises(MODULE.SubmissionError, match="schema validation"):
        MODULE.publish_submission(root, "alice", _body(parser, envelope))


def test_path_traversal_parser_id_is_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    parser = _parser()
    parser["id"] = "it../escape.internet"
    envelope = _envelope(parser)
    with pytest.raises(MODULE.SubmissionError, match="schema validation"):
        MODULE.publish_submission(root, "alice", _body(parser, envelope))


def test_invalid_regex_is_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    parser = _parser()
    parser["fields"]["amount"]["candidates"][0]["regex"] = "(unclosed"
    with pytest.raises(MODULE.SubmissionError, match="invalid regex"):
        MODULE.publish_submission(root, "alice", _body(parser))


def test_higher_version_can_replace_verified_and_returns_experimental(tmp_path: Path):
    root = _repo(tmp_path)
    first = MODULE.publish_submission(root, "alice", _body(_parser(1)))
    saved = yaml.safe_load((root / first["path"]).read_text())
    saved["metadata"]["status"] = "verified"
    saved["metadata"]["quality"] = "verified"
    (root / first["path"]).write_text(yaml.safe_dump(saved, sort_keys=False))

    second = MODULE.publish_submission(root, "bob", _body(_parser(2)))
    updated = yaml.safe_load((root / second["path"]).read_text())

    assert second["update"] is True
    assert updated["version"] == 2
    assert updated["metadata"]["status"] == "experimental"
    assert updated["metadata"]["quality"] == "experimental"
    assert updated["metadata"]["submitted_by"] == "bob"


def test_same_version_with_different_content_is_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    MODULE.publish_submission(root, "alice", _body(_parser(2)))
    changed = _parser(2)
    changed["metadata"]["name"] = "Changed"
    with pytest.raises(MODULE.SubmissionError, match="version must increase"):
        MODULE.publish_submission(root, "alice", _body(changed))


def test_lower_version_is_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    MODULE.publish_submission(root, "alice", _body(_parser(2)))
    with pytest.raises(MODULE.SubmissionError, match="greater than current"):
        MODULE.publish_submission(root, "alice", _body(_parser(1)))


def test_same_issue_is_idempotent(tmp_path: Path):
    root = _repo(tmp_path)
    body = _body(_parser(1))
    first = MODULE.publish_submission(root, "alice", body)
    second = MODULE.publish_submission(root, "alice", body)
    assert first["already_processed"] is False
    assert second["already_processed"] is True


def test_sensitive_static_values_are_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    parser = _parser()
    parser["fields"]["customer_code"] = {"value": "123456789"}
    with pytest.raises(MODULE.SubmissionError, match="sensitive field"):
        MODULE.publish_submission(root, "alice", _body(parser))
