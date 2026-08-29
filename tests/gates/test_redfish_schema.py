"""Schema gate: every captured fixture conforms to its DMTF Redfish schema.

Skipped by default unless the schema bundle is vendored (run tools/fetch_schemas.sh
once). This keeps the offline unit suite green without a network/schema dependency,
while CI can vendor the schemas and enforce the gate. OEM-typed resources (e.g.
Dell-only types) are skipped — there is no standard schema for them.

Author Mus spyroot@gmail.com
"""
import glob
import json
import pathlib

import pytest

pytest.importorskip("referencing")

_SCHEMA_DIR = pathlib.Path(__file__).resolve().parents[2] / "tools" / "redfish-schemas"
if not (_SCHEMA_DIR.exists() and any(_SCHEMA_DIR.glob("*.json"))):
    pytest.skip(
        "Redfish schemas not vendored; run tools/fetch_schemas.sh",
        allow_module_level=True,
    )

import os  # noqa: E402

from tools.redfish_validate import SchemaUnavailable, validate_payload  # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parents[2]
# Our hand-authored iDRAC fixtures are the gate. The captured DMTF mockup tree
# (json_responses/) is third-party reference data from older schema versions;
# validate it only when REDFISH_SCHEMA_ALL=1.
_FIXTURES = sorted(glob.glob(str(_ROOT / "tests" / "idrac_fixtures" / "*.json")))
if os.environ.get("REDFISH_SCHEMA_ALL"):
    _FIXTURES += sorted(glob.glob(str(_ROOT / "idrac_ctl" / "json_responses" / "*.json")))


@pytest.mark.schema
@pytest.mark.parametrize("path", _FIXTURES, ids=lambda p: pathlib.Path(p).name)
def test_fixture_conforms_to_schema(path):
    """A captured fixture validates against its standard Redfish schema."""
    payload = json.loads(pathlib.Path(path).read_text())
    if "@odata.type" not in payload:
        pytest.skip("no @odata.type (collection/metadata)")
    try:
        errors = validate_payload(payload)
    except SchemaUnavailable as exc:
        pytest.skip(str(exc))  # OEM type / no standard schema
    assert not errors, "\n".join(
        f"{list(e.absolute_path)}: {e.message}" for e in errors[:10]
    )
