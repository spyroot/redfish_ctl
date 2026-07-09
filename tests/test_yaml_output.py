"""`--yaml` output rendering: the same payload serialized as YAML instead of JSON."""
import argparse

import yaml as _yaml

from redfish_ctl.redfish_main import json_printer


def _args(**kw):
    base = dict(no_stdout=False, json_only=True, yaml=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_yaml_flag_renders_parseable_yaml(capsys):
    """--yaml renders the payload as YAML that round-trips back to the same object."""
    data = {"System": "System_0", "PowerState": "On", "Sol": [{"Manager": "BMC_0"}]}
    json_printer(data, _args(yaml=True), colorized=False)
    out = capsys.readouterr().out
    assert _yaml.safe_load(out) == data
    assert not out.lstrip().startswith("{")  # not JSON


def test_default_is_json(capsys):
    """Without --yaml the output is JSON (starts with '{')."""
    json_printer({"a": 1}, _args(yaml=False), colorized=False)
    assert capsys.readouterr().out.strip().startswith("{")
