from __future__ import annotations

import importlib.util
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
    (root / "scripts").mkdir(parents=True, exist_ok=True)
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
            "min_billy_version": "0.9.1",
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
                    {
                        "source": "email",
                        "regex": r"Totale\s+(?P<value>[0-9.,]+)",
                    }
                ],
                "transform": {"type": "decimal", "locale": "it_IT"},
            },
        },
    }


def _body(parser: dict) -> str:
    content = yaml.safe_dump(parser, sort_keys=False, allow_unicode=True)
    return f"{MODULE.MARKER}\n\n```yaml\n{content}```\n"


def test_new_submission_is_forced_experimental_and_owned(tmp_path: Path):
    root = _repo(tmp_path)
    parser = _parser()
    parser["metadata"]["quality"] = "verified"
    parser["metadata"]["submitted_by"] = "someone-else"

    result = MODULE.publish_submission(root, "alice", _body(parser))

    assert result["parser_id"] == "it.community.internet"
    assert result["quality"] == "experimental"
    saved = yaml.safe_load((root / result["path"]).read_text())
    assert saved["metadata"]["quality"] == "experimental"
    assert saved["metadata"]["submitted_by"] == "alice"


def test_owner_can_publish_higher_version(tmp_path: Path):
    root = _repo(tmp_path)
    first = MODULE.publish_submission(root, "alice", _body(_parser(1)))
    assert first["update"] is False

    second = MODULE.publish_submission(root, "Alice", _body(_parser(2)))
    assert second["update"] is True
    assert yaml.safe_load((root / second["path"]).read_text())["version"] == 2


def test_other_user_cannot_replace_experimental_parser(tmp_path: Path):
    root = _repo(tmp_path)
    MODULE.publish_submission(root, "alice", _body(_parser(1)))

    with pytest.raises(MODULE.SubmissionError, match="owned by @alice"):
        MODULE.publish_submission(root, "bob", _body(_parser(2)))


def test_official_parser_cannot_be_replaced(tmp_path: Path):
    root = _repo(tmp_path)
    parser = yaml.safe_load((root / "parsers/it/eon/electricity.yaml").read_text())
    parser["version"] = int(parser["version"]) + 1

    with pytest.raises(MODULE.SubmissionError, match="Only experimental"):
        MODULE.publish_submission(root, "alice", _body(parser))


def test_sensitive_static_values_are_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    parser = _parser()
    parser["fields"]["customer_code"] = {"value": "123456789"}

    with pytest.raises(MODULE.SubmissionError, match="sensitive field"):
        MODULE.publish_submission(root, "alice", _body(parser))


def test_version_must_increase(tmp_path: Path):
    root = _repo(tmp_path)
    MODULE.publish_submission(root, "alice", _body(_parser(2)))

    with pytest.raises(MODULE.SubmissionError, match="must increase"):
        MODULE.publish_submission(root, "alice", _body(_parser(2)))
