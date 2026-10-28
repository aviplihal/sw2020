"""Pure tests: the error codes at SolidWorksError raise sites (read from the source,
since importing session.py needs pywin32)."""

import ast
import pathlib
import re

_SOURCES = ["src/solidworks_mcp/session.py", "src/solidworks_mcp/kb"]


def _raises():
    """(file, line, code or None, params node or None) for every SolidWorksError(...) call."""
    files = []
    for source in _SOURCES:
        path = pathlib.Path(source)
        files += sorted(path.rglob("*.py")) if path.is_dir() else [path]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "SolidWorksError":
                keywords = {k.arg: k.value for k in node.keywords}
                code = keywords.get("code")
                yield path.name, node.lineno, code, keywords.get("params")


def test_codes_are_constant_upper_snake_names():
    for name, line, code, _ in _raises():
        if code is None:
            continue
        if isinstance(code, ast.Attribute) and ast.unparse(code) == "exc.code":
            continue  # passed on unchanged (run_guarded)
        assert isinstance(code, ast.Constant), f"{name}:{line}: a code must be a literal, so it stays stable"
        assert re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", code.value), f"{name}:{line}: {code.value!r}"


def test_params_are_a_dict():
    for name, line, _, params in _raises():
        if params is not None and ast.unparse(params) != "exc.params":
            assert isinstance(params, ast.Dict), f"{name}:{line}: params must be a dict of values"


def test_the_first_tool_families_have_codes():
    """Fillet, full round, chamfer and shell: every failure they raise is coded."""
    source = pathlib.Path("src/solidworks_mcp/session.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    family = {"add_fillet", "_variable_fillet", "add_full_round", "_full_round_sides", "add_chamfer",
              "_insert_chamfer", "add_shell"}
    uncoded = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in family:
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and getattr(call.func, "id", None) == "SolidWorksError"
                        and not any(k.arg == "code" for k in call.keywords)):
                    uncoded.append(f"{node.name}:{call.lineno}")
    assert not uncoded, f"uncoded failures: {uncoded}"
