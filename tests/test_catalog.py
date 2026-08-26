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


def test_catalog_status_tracks_parser_lifecycle():
    assert MODULE._catalog_status({"status": "verified"}) == "verified"
    assert MODULE._catalog_status({"status": "experimental"}) == "experimental"
    assert MODULE._catalog_status({"status": "outdated"}) == "outdated"
    assert MODULE._catalog_status({"quality": "experimental"}) == "experimental"
    assert MODULE._catalog_status({"deprecated": True}) == "outdated"
