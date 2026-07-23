"""Offline tests for the exception-interface gate (tools/exception_interface_gate.py).

Custom exception types must be defined only in the interface modules
(redfish_ctl/cmd_exceptions.py and redfish_ctl/redfish_exceptions.py); a new exception
class anywhere else fails the gate, so the error contract the single top-level handler
maps to exit codes stays in one place.

Author Mus <spyroot@gmail.com>
"""
import ast

from tools import exception_interface_gate as gate


def _base(src):
    """Return the first base-class node of a one-class source snippet.

    :param src: Python source defining exactly one class.
    :return: the first base expression of that class.
    """
    return ast.parse(src).body[0].bases[0]


def test_exception_base_detected():
    """A class subclassing an Error/Exception base is recognized as an exception."""
    assert gate._is_exception_base(_base("class F(RuntimeError): pass"))
    assert gate._is_exception_base(_base("class F(RedfishException): pass"))
    assert gate._is_exception_base(_base("class F(requests.HTTPError): pass"))


def test_non_exception_base_ignored():
    """A class with a non-exception base is not flagged."""
    assert not gate._is_exception_base(_base("class F(dict): pass"))
    assert not gate._is_exception_base(_base("class F(object): pass"))


def test_real_repo_gate_is_clean():
    """The repo passes: no exception class outside the interface beyond the baseline."""
    assert gate.main() == 0
