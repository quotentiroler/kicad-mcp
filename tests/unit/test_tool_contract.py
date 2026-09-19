"""Every registered tool has to behave when handed a path that is not there.

An MCP tool that raises takes the client down with it; the contract is to
return something describing the failure.  And a tool that reports success
on a failed subprocess is worse than one that raises, because nothing
downstream has any way to tell.

Nothing here needs KiCad, a project, or a board: the point is what happens
when none of those exist.
"""

import asyncio
import inspect

import pytest

from kicad_mcp.server import create_server

MISSING = "/definitely/not/here/nope.kicad_pro"


def _args(tool):
    """Plausible arguments for one tool, from its own schema."""
    schema = getattr(tool, "parameters", None) or {}
    props = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    out = {}
    for name, spec in props.items():
        if name not in required:
            continue
        kind = spec.get("type")
        if kind == "string":
            out[name] = MISSING if "path" in name or "file" in name or "dir" in name else "x"
        elif kind == "integer":
            out[name] = 1
        elif kind == "number":
            out[name] = 1.0
        elif kind == "boolean":
            out[name] = False
        elif kind == "array":
            out[name] = []
        elif kind == "object":
            out[name] = {}
        else:
            out[name] = MISSING
    return out


def _all_tools():
    mcp = create_server()
    return asyncio.run(mcp.list_tools()), mcp


TOOLS, MCP = _all_tools()


def test_every_tool_is_registered_once():
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names)), "a tool name is registered twice"
    assert len(names) > 40, f"only {len(names)} tools registered"


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_missing_path_does_not_raise(tool):
    """A bad path is an error to report, not an exception to escape."""
    fn = getattr(tool, "fn", None)
    if fn is None:
        pytest.skip("no callable behind this tool")
    kwargs = _args(tool)
    try:
        result = fn(**kwargs)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
    except TypeError as e:
        pytest.skip(f"cannot synthesise arguments: {e}")
    except Exception as e:  # noqa: BLE001 - that is the thing under test
        pytest.fail(f"{tool.name} raised {type(e).__name__}: {e}")


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_missing_path_is_not_reported_as_success(tool):
    """Nothing may answer success=True about a project that is not there."""
    fn = getattr(tool, "fn", None)
    if fn is None:
        pytest.skip("no callable behind this tool")
    try:
        result = fn(**_args(tool))
        if inspect.isawaitable(result):
            result = asyncio.run(result)
    except Exception:  # noqa: BLE001 - covered by the test above
        pytest.skip("raised; that is the other test's finding")
    if isinstance(result, dict) and result.get("success") is True:
        pytest.fail(f"{tool.name} reports success for {MISSING}: {result}")
