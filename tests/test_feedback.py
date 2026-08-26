from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "process_feedback", ROOT / "scripts" / "process_feedback.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "schema", root / "schema")
    shutil.copytree(ROOT / "parsers", root / "parsers")
    return root


def _fingerprint(index: int) -> str:
    return f"{index:064x}"


def _body(
    *,
    parser_id: str = "it.a2aenergia.energy",
    version: int = 1,
    result: str = "working",
    fingerprint: str | None = None,
) -> str:
    payload = {
        "schema_version": 1,
        "parser_id": parser_id,
        "version": version,
        "result": result,
        "installation_fingerprint": fingerprint or _fingerprint(1),
        "billy_version": "0.11.3",
        "source_commit": "abc123",
    }
    return f"{MODULE.MARKER}\n\n```json\n{json.dumps(payload)}\n```\n"


def _process_many(root: Path, votes: list[str]) -> list[dict]:
    results = []
    for index, vote in enumerate(votes, start=1):
        results.append(
            MODULE.process_feedback(
                root,
                _body(result=vote, fingerprint=_fingerprint(index)),
                now=f"2026-08-26T12:00:{index:02d}+00:00",
            )
        )
    return results


def test_working_partial_and_failed_are_recorded(tmp_path: Path):
    root = _repo(tmp_path)
    _process_many(root, ["working", "partial", "failed"])
    store = json.loads((root / "feedback/it/a2aenergia/energy/v1.json").read_text())
    assert sorted(store["votes"].values()) == ["failed", "partial", "working"]


def test_invalid_fingerprint_is_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    with pytest.raises(MODULE.FeedbackError, match="schema validation"):
        MODULE.process_feedback(root, _body(fingerprint="not-a-sha256"))


def test_duplicate_vote_is_idempotent(tmp_path: Path):
    root = _repo(tmp_path)
    first = MODULE.process_feedback(root, _body(), now="2026-08-26T12:00:00+00:00")
    second = MODULE.process_feedback(root, _body(), now="2026-08-26T12:01:00+00:00")
    assert first["changed"] is True
    assert second["changed"] is False
    assert second["feedback"]["contributors"] == 1


def test_same_fingerprint_can_change_vote(tmp_path: Path):
    root = _repo(tmp_path)
    MODULE.process_feedback(root, _body(result="working"))
    changed = MODULE.process_feedback(root, _body(result="failed"))
    assert changed["changed"] is True
    assert changed["previous_result"] == "working"
    assert changed["feedback"] == {
        "working": 0,
        "partial": 0,
        "failed": 1,
        "contributors": 1,
    }


def test_missing_parser_is_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    with pytest.raises(MODULE.FeedbackError, match="does not exist"):
        MODULE.process_feedback(root, _body(parser_id="it.missing.energy"))


def test_non_current_version_is_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    with pytest.raises(MODULE.FeedbackError, match="not the current version"):
        MODULE.process_feedback(root, _body(version=2))


def test_four_working_stays_experimental(tmp_path: Path):
    root = _repo(tmp_path)
    result = _process_many(root, ["working"] * 4)[-1]
    assert result["promoted"] is False
    parser = yaml.safe_load((root / "parsers/it/a2aenergia/energy.yaml").read_text())
    assert parser["metadata"]["status"] == "experimental"


def test_five_working_promotes_verified(tmp_path: Path):
    root = _repo(tmp_path)
    result = _process_many(root, ["working"] * 5)[-1]
    parser = yaml.safe_load((root / "parsers/it/a2aenergia/energy.yaml").read_text())
    assert result["promoted"] is True
    assert parser["metadata"]["status"] == "verified"
    assert parser["metadata"]["quality"] == "verified"


def test_five_working_one_partial_promotes_verified(tmp_path: Path):
    root = _repo(tmp_path)
    result = _process_many(root, ["working"] * 5 + ["partial"])[-1]
    assert result["status"] == "verified"
    assert result["feedback"]["contributors"] == 6


def test_five_working_two_failed_stays_experimental(tmp_path: Path):
    root = _repo(tmp_path)
    result = _process_many(root, ["working"] * 5 + ["failed", "failed"])[-1]
    assert result["promoted"] is False
    parser = yaml.safe_load((root / "parsers/it/a2aenergia/energy.yaml").read_text())
    assert parser["metadata"]["status"] == "experimental"


def test_new_version_has_isolated_feedback(tmp_path: Path):
    root = _repo(tmp_path)
    _process_many(root, ["working"] * 5)
    parser_path = root / "parsers/it/a2aenergia/energy.yaml"
    parser = yaml.safe_load(parser_path.read_text())
    parser["version"] = 2
    parser["metadata"]["status"] = "experimental"
    parser["metadata"]["quality"] = "experimental"
    parser_path.write_text(yaml.safe_dump(parser, sort_keys=False))

    result = MODULE.process_feedback(root, _body(version=2, fingerprint=_fingerprint(10)))
    assert result["feedback"]["contributors"] == 1
    assert (root / "feedback/it/a2aenergia/energy/v1.json").exists()
    assert (root / "feedback/it/a2aenergia/energy/v2.json").exists()


def test_catalog_contains_only_aggregate_feedback(tmp_path: Path):
    root = _repo(tmp_path)
    fingerprint = _fingerprint(42)
    MODULE.process_feedback(root, _body(fingerprint=fingerprint))
    catalog = MODULE._BUILD.build_catalog(root)
    item = next(item for item in catalog["parsers"] if item["id"] == "it.a2aenergia.energy")
    assert item["feedback"] == {
        "working": 1,
        "partial": 0,
        "failed": 0,
        "contributors": 1,
    }
    assert fingerprint not in json.dumps(catalog)
