"""Unit tests for the exception-interface gate's detection logic.

The gate keeps exception types defined only in the exception interface
(``redfish_ctl/cmd_exceptions.py``, ``redfish_ctl/redfish_exceptions.py``). These
tests pin the transitive detection so a future edit cannot silently reopen the
subclass bypass or start flagging non-exception classes.

Author Mus spyroot@gmail.com
"""
import importlib.util
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "exception_interface_gate", REPO_ROOT / "tools" / "exception_interface_gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def test_suffix_names_are_exceptions():
    """A base ending in Error/Exception marks its class as an exception type."""
    assert gate._suffix_exc("RuntimeError")
    assert gate._suffix_exc("HTTPException")
    assert not gate._suffix_exc("Mapping")
    assert not gate._suffix_exc("ConfigurationConflict")


def test_direct_builtin_subclass_is_detected():
    """A class subclassing a builtin exception is an exception type."""
    classes = [("a.py", 1, "MyError", ["RuntimeError"])]
    assert gate._exception_class_names(classes) == {"MyError"}


def test_transitive_subclass_bypass_is_closed():
    """Subclassing a project exception whose own name is not suffixed is still caught.

    ``ConfigurationConflict(RuntimeError)`` is an exception; a class deriving from it
    (``SneakyErr(ConfigurationConflict)``) must also be detected even though its base
    name does not end in Error/Exception — otherwise the gate is trivially bypassable.
    """
    classes = [
        ("config.py", 34, "ConfigurationConflict", ["RuntimeError"]),
        ("sneaky.py", 9, "SneakyState", ["ConfigurationConflict"]),
    ]
    names = gate._exception_class_names(classes)
    assert {"ConfigurationConflict", "SneakyState"} <= names


def test_non_exception_class_is_not_flagged():
    """A plain class (no exception base) is never treated as an exception type."""
    classes = [("plain.py", 3, "PlainData", ["object"]),
               ("plain.py", 20, "Mapping", [])]
    assert gate._exception_class_names(classes) == set()


def test_interface_modules_are_not_reported():
    """An exception defined IN the interface is allowed; one outside it is a violation."""
    classes = [
        ("redfish_ctl/cmd_exceptions.py", 5, "InvalidArgument", ["RuntimeError"]),
        ("redfish_ctl/network/cmd_x.py", 7, "LocalError", ["RuntimeError"]),
    ]
    names = gate._exception_class_names(classes)
    reported = sorted(
        f"{f}:{line}" for f, line, name, _bases in classes
        if f not in gate._INTERFACE and name in names)
    assert reported == ["redfish_ctl/network/cmd_x.py:7"]


def test_baseline_skips_comments_and_blank_lines():
    """The committed baseline parses to the five grandfathered locations only."""
    base = gate._baseline()
    assert "redfish_ctl/network/cmd_network_adapter_reset.py:25" in base
    assert all(not entry.startswith("#") for entry in base)
    assert len(base) == 5
