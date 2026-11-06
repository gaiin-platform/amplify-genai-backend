"""The document retention admin config: its schema accepts exactly {isActive, days} and it is wired through the service."""
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from schemata.update_admin_config_schema import update_admin_config_schema as schema  # noqa: E402

SOURCE = (ROOT / "service" / "core.py").read_text()


def check(data):
    jsonschema.validate({"configurations": [{"type": "documentRetention", "data": data}]}, schema)


def test_a_valid_policy_is_accepted():
    check({"isActive": True, "days": 90})
    check({"isActive": False, "days": 1})


@pytest.mark.parametrize("bad", [
    {"isActive": True},                              # days missing
    {"days": 90},                                    # isActive missing
    {"isActive": True, "days": 0},                   # must be at least a day
    {"isActive": True, "days": 3651},                # capped at ten years
    {"isActive": True, "days": 30.5},                # whole days only
    {"isActive": True, "days": "90"},                # a number, not text
    {"isActive": "yes", "days": 90},
    {"isActive": True, "days": 90, "dryRun": True},  # nothing else is accepted
])
def test_anything_else_is_refused(bad):
    with pytest.raises(jsonschema.ValidationError):
        check(bad)


def test_it_is_saved_loaded_and_starts_switched_off():
    assert 'DOCUMENT_RETENTION = "documentRetention"' in SOURCE
    assert SOURCE.count("AdminConfigTypes.DOCUMENT_RETENTION") >= 3              # save, lazy load, default
    default = SOURCE.split("elif config_type == AdminConfigTypes.DOCUMENT_RETENTION:", 1)[1].split("elif", 1)[0]
    assert '{"isActive": False, "days": 90}' in default                            # off by default: nothing deletes by itself
