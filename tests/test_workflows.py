from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "workflow",
    [
        "community-submission.yml",
        "community-feedback.yml",
        "build-catalog.yml",
    ],
)
def test_community_write_workflows_queue_pending_runs(workflow: str):
    config = yaml.safe_load((ROOT / ".github" / "workflows" / workflow).read_text())
    assert config["concurrency"] == {
        "group": "billy-parser-community-writes",
        "cancel-in-progress": False,
        "queue": "max",
    }
