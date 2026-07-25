"""Unit tests for the single-connection gate's detection logic.

The gate keeps the BMC endpoint/credential set declared in one place (the base
manager plus ``redfish_ctl/config.py``) and removes redundant pass-through
``__init__`` methods. These tests pin the AST predicates so a future edit cannot
silently start deleting real constructors or stop flagging a re-declared
endpoint on a manager class.

Author Mus spyroot@gmail.com
"""
import ast
import importlib.util
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "single_connection_gate", REPO_ROOT / "tools" / "single_connection_gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _first_class_init(src):
    """Parse ``src`` and return its first class and that class's ``__init__``.

    :param src: Python source defining at least one class.
    :return: a (ClassDef, FunctionDef|None) tuple for the first class found.
    """
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    init = next((n for n in cls.body
                 if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
    return cls, init


def test_passthrough_init_is_detected():
    """A ``(self, *args, **kwargs)`` body that only calls ``super().__init__`` is a pass-through."""
    _, init = _first_class_init(
        "class Foo(IDracManager):\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        '''doc'''\n"
        "        super(Foo, self).__init__(*args, **kwargs)\n")
    assert gate._is_passthrough_init(init)


def test_real_init_is_not_passthrough():
    """An ``__init__`` that assigns state does real work and is never removed."""
    _, init = _first_class_init(
        "class Foo(IDracManager):\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        super().__init__(*args, **kwargs)\n"
        "        self.x = 1\n")
    assert not gate._is_passthrough_init(init)


def test_bare_super_without_forwarding_is_not_passthrough():
    """``super().__init__()`` not forwarding ``*args/**kwargs`` is not the boilerplate form."""
    _, init = _first_class_init(
        "class Foo(IDracManager):\n"
        "    def __init__(self):\n"
        "        super().__init__()\n")
    assert not gate._is_passthrough_init(init)


def test_manager_class_detected_by_base_name():
    """A base whose name ends in ``Manager`` puts the class in scope for the endpoint rule."""
    cls, _ = _first_class_init("class Foo(IDracManager):\n    def __init__(self): pass\n")
    assert gate._is_manager_class(cls)
    plain, _ = _first_class_init("class Bar:\n    def __init__(self, host): pass\n")
    assert not gate._is_manager_class(plain)


def test_endpoint_params_scoped_to_managers():
    """A manager re-declaring host/username/password is a second endpoint; a plain
    request payload with a ``host`` field is out of scope."""
    cls_mgr, init_mgr = _first_class_init(
        "class Foo(RedfishManager):\n"
        "    def __init__(self, host, username, password): pass\n")
    assert gate._is_manager_class(cls_mgr)
    assert gate._endpoint_params(init_mgr) == ["host", "password", "username"]
    cls_req, _ = _first_class_init(
        "class ShareReq:\n    def __init__(self, host='downloads.example'): pass\n")
    assert not gate._is_manager_class(cls_req)
