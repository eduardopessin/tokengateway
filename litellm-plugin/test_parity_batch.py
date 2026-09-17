"""Behaviours measured on the wire, pinned so they cannot silently regress.

Every expectation here comes from a request issued against the real upstream,
not from documentation. The measurement is quoted next to each case.
"""
import ast
import json
import os
import sys
import time
import uuid
from pathlib import Path

MODULE = Path(__file__).with_name("sitecustomize.py")
TREE = ast.parse(MODULE.read_text())


def load(names, const_prefixes):
    """Extract pure helpers by AST; importing the plugin needs a live proxy."""
    functions = [
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    missing = set(names) - {node.name for node in functions}
    assert not missing, f"missing: {missing}"
    constants = [
        node for node in TREE.body
        if isinstance(node, ast.Assign)
        and any(
            getattr(target, "id", "").startswith(const_prefixes)
            for target in node.targets
        )
    ]
    # Some module constants evaluate uuid/time at import time.
    namespace = {"uuid": uuid, "json": json, "os": os, "time": time}
    exec(compile(ast.Module(body=constants + functions, type_ignores=[]),
                 str(MODULE), "exec"), namespace)
    return namespace


def test_adaptive_is_the_default_for_unknown_models():
    ns = load(["_is_anthropic_adaptive"], ("_ANTHROPIC_",))
    adaptive = ns["_is_anthropic_adaptive"]
    # Measured: budget thinking on claude-opus-5 returns 0 chars of reasoning
    # while adaptive returns 82, so guessing budget fails silently. Guessing
    # adaptive fails loudly (400 "adaptive thinking is not supported").
    for model in ("claude-opus-5", "claude-fable-5", "claude-sonnet-5",
                  "claude-opus-4-8", "claude-opus-4-6", "claude-sonnet-4-6"):
        assert adaptive(model), model
    # Measured: these three answer 400 to adaptive and reason fine on budget.
    for model in ("claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"):
        assert not adaptive(model), model
    # An alias or a model we have not enumerated must land on the detectable side.
    for model in ("claude-opus", "claude-sonnet", "claude-opus-6", "claude-*"):
        assert adaptive(model), model


def test_top_efforts_reach_the_wire():
    ns = load(["_is_anthropic_adaptive"], ("_ANTHROPIC_",))
    effort = ns["_ANTHROPIC_ADAPTIVE_EFFORT"]
    # Measured: xhigh and max are accepted and produce more output than high
    # (out=164 high, 273 xhigh, 275 max), so they must not collapse into high.
    assert effort["xhigh"] == "xhigh"
    assert effort["max"] == "max"
    assert effort["minimal"] == "low", "the wire has no minimal tier"


def test_google_finish_reason_distinguishes_truncation_from_stop():
    ns = load(["_google_finish_reason"], ("_GOOGLE_FINISH",))
    reason = ns["_google_finish_reason"]
    assert reason("STOP", False) == "stop"
    assert reason("MAX_TOKENS", False) == "length"
    assert reason("SAFETY", False) == "content_filter"
    assert reason("RECITATION", False) == "content_filter"
    assert reason("MALFORMED_FUNCTION_CALL", False) == "content_filter"
    # A tool call is the outcome even when the turn also hit the token ceiling.
    assert reason("STOP", True) == "tool_calls"
    assert reason("MAX_TOKENS", True) == "tool_calls"
    # An absent reason must not be invented as an error.
    assert reason(None, False) == "stop"


def test_codex_incomplete_is_length():
    ns = load(["_codex_finish_reason"], ("_CODEX_WIRE",))
    reason = ns["_codex_finish_reason"]
    assert reason("completed", False) == "stop"
    assert reason("incomplete", False) == "length"
    assert reason(None, False) == "stop"
    assert reason("incomplete", True) == "tool_calls"


def test_planning_leak_filter_is_flash_only():
    ns = load(["_google_is_planning_leak", "_google_is_flash_leak_model"], ("_GOOGLE_LEAK",))
    leak = ns["_google_is_planning_leak"]
    # A leak shape without the `thought` key used to reach the client verbatim.
    assert leak('{"call":"read","_i":1}', "gemini-3.8-flash-low")
    assert leak('{"thought":"planning"}', "gemini-3.8-flash-low")
    assert leak('{"path":"a.py","content":"x"}', "gemini-3.8-flash-low")
    # Only flash leaks planning; a pro model answering JSON must survive.
    assert not leak('{"command":"ls"}', "gemini-3.1-pro-low")
    assert not leak('{"thought":"planning"}', "gemini-pro-agent")
    # Prose, and JSON without planning keys, are never swallowed.
    assert not leak("Here is the plan.", "gemini-3.8-flash-low")
    assert not leak('{"answer":42}', "gemini-3.8-flash-low")


def test_codex_aliases_never_rename_a_version():
    ns = load(["_codex_finish_reason"], ("_CODEX_WIRE",))
    aliases = ns["_CODEX_WIRE_ALIASES"]
    # Family aliases are honest; a version alias bills the client for a model
    # that never ran.
    assert aliases["codex"] == "gpt-5.5"
    assert "gpt-5.4" not in aliases
    assert "gpt-5.4-mini" not in aliases


def test_bridge_result_tuples_are_unpacked_consistently():
    """Every caller must unpack exactly what the bridge entry point returns.

    Adding the finish reason to these tuples broke four call sites at a
    distance; the request answered `500 too many values to unpack (expected 4)`
    for every non-streaming Gemini turn. An arity mismatch here is invisible
    until a request hits that exact path, so it is checked statically.
    """
    def returned_arity(func_name):
        for node in ast.walk(TREE):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                widths = {
                    len(child.value.elts)
                    for child in ast.walk(node)
                    if isinstance(child, ast.Return) and isinstance(child.value, ast.Tuple)
                }
                assert len(widths) == 1, f"{func_name} returns tuples of {widths}"
                return widths.pop()
        raise AssertionError(f"{func_name} not found")

    def unpack_widths(func_name):
        widths = []
        for node in ast.walk(TREE):
            if not isinstance(node, ast.Assign):
                continue
            call = node.value
            if isinstance(call, ast.Await):
                call = call.value
            if not isinstance(call, ast.Call):
                continue
            callee = call.func
            name = getattr(callee, "id", None) or getattr(callee, "attr", None)
            called = name == func_name or any(
                getattr(arg, "id", None) == func_name for arg in call.args
            )
            if not called:
                continue
            target = node.targets[0]
            widths.append(len(target.elts) if isinstance(target, ast.Tuple) else 1)
        assert widths, f"no call site found for {func_name}"
        return widths

    for func_name in ("_call_antigravity_sync", "_call_codex_sync", "_antigravity_collect"):
        expected = returned_arity(func_name)
        for width in unpack_widths(func_name):
            assert width == expected, (
                f"{func_name} returns {expected} values, a caller unpacks {width}"
            )


def test_antigravity_never_serves_a_name_it_was_not_asked_for():
    ns = load(
        ["_map_antigravity_model", "_antigravity_base_family"],
        ("_ANTIGRAVITY_EFFORT", "_ANTIGRAVITY_SUFFIXES", "_ANTIGRAVITY_BROKEN",
         "_ANTIGRAVITY_MODEL_CACHE"),
    )
    resolve = ns["_map_antigravity_model"]
    # The real catalogue of this account, obtained with :fetchAvailableModels.
    ns["_ANTIGRAVITY_MODEL_CACHE"]["ids"] = (
        "gemini-3.8-flash-low", "gemini-3.8-flash-medium", "gemini-3.8-flash-high",
        "gemini-3.8-flash-tiered", "gemini-3.1-pro-low", "gemini-pro-agent",
        "gemini-2.5-flash-thinking",
    )
    # The effort picks the variant when the client asks for the family.
    assert resolve("gemini-3.8-flash", "medium") == "gemini-3.8-flash-medium"
    # A served variant asked for by name is respected, not peeled off.
    assert resolve("gemini-3.8-flash-tiered", "medium") == "gemini-3.8-flash-tiered"
    assert resolve("gemini-2.5-flash-thinking", "low") == "gemini-2.5-flash-thinking"
    # gemini-3.1-pro-high is in the catalogue but listed in deprecatedModelIds
    # and returns 400 INVALID_ARGUMENT, so the top of the scale routes to the
    # agent variant.
    assert resolve("gemini-3.1-pro", "high") == "gemini-pro-agent"
    # A name the upstream does not serve must fail loudly instead of being
    # served by another variant with the `model` field echoing the requested
    # name.
    for fake in ("gemini-3.8-flash-thinking", "gemini-invented-9"):
        try:
            served = resolve(fake, "medium")
        except Exception:
            continue
        raise AssertionError(f"{fake} was served as {served}")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
