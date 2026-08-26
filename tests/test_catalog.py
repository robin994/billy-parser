from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_catalog", ROOT / "scripts" / "build_catalog.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_catalog_validates():
    catalog = MODULE.build_catalog()
    assert catalog["schema_version"] == 1
    assert any(item["id"] == "it.eon.electricity" for item in catalog["parsers"])
    statuses = {item["status"] for item in catalog["parsers"]}
    assert statuses <= {"experimental", "verified", "outdated"}
    assert any(item["status"] == "experimental" for item in catalog["parsers"])
    assert any(item["status"] == "verified" for item in catalog["parsers"])


def test_catalog_v2_groups_parsers_by_country():
    legacy = {
        "generated_at": "2026-08-26T00:00:00+00:00",
        "source_commit": "abc123",
        "parsers": [
            {"id": "it.demo.energy", "country": "IT"},
            {"id": "fr.demo.energy", "country": "FR"},
            {"id": "it.other.internet", "country": "IT"},
        ],
    }

    index, shards = MODULE.build_catalog_v2(legacy)

    assert index["schema_version"] == 2
    assert index["countries"] == {
        "FR": {"path": "catalog/fr.json", "parsers": 1},
        "IT": {"path": "catalog/it.json", "parsers": 2},
    }
    assert [item["id"] for item in shards["IT"]["parsers"]] == [
        "it.demo.energy",
        "it.other.internet",
    ]
    assert [item["id"] for item in shards["FR"]["parsers"]] == ["fr.demo.energy"]


def test_catalog_v2_current_repo_exposes_only_italian_shard():
    catalog = MODULE.build_catalog()
    index, shards = MODULE.build_catalog_v2(catalog)

    assert index["countries"]["IT"]["path"] == "catalog/it.json"
    assert index["countries"]["IT"]["parsers"] == len(catalog["parsers"])
    assert set(shards) == {"IT"}
    assert all(item["country"] == "IT" for item in shards["IT"]["parsers"])


def test_catalog_status_tracks_parser_lifecycle():
    assert MODULE._catalog_status({"status": "verified"}) == "verified"
    assert MODULE._catalog_status({"status": "experimental"}) == "experimental"
    assert MODULE._catalog_status({"status": "outdated"}) == "outdated"
    assert MODULE._catalog_status({"quality": "experimental"}) == "experimental"
    assert MODULE._catalog_status({"deprecated": True}) == "outdated"
