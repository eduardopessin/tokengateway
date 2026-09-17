"""Cache breakpoints must follow the tail of a tool-driven conversation.

Regression: the anchors only accepted plain user/assistant text, so on a turn
ending in a tool result the rolling window stayed pinned to the first user
message. Measured against the live proxy before the fix: claude-opus-5 read
2876 of 8697 prompt tokens, so 67% was re-read at full price every turn.
"""
import ast
import sys
from pathlib import Path

MODULE = Path(__file__).with_name("sitecustomize.py")


def load_plugin():
    """Extract the pure helpers by AST; the plugin hooks a live proxy on import."""
    tree = ast.parse(MODULE.read_text())
    # Only the entry points are required, so this harness also loads a version
    # without the newer helpers and lets the behavioural asserts do the talking.
    required = {
        "_anthropic_cache_control",
        "_anthropic_markable_message",
        "_count_cache_breakpoints",
        "_mark_cache_breakpoint",
        "_apply_conversation_cache",
    }
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and (node.name in required or node.name.startswith("_anthropic_"))
    ]
    missing = required - {node.name for node in functions}
    assert not missing, f"missing: {missing}"
    constants = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            getattr(target, "id", "").startswith(("ANTHROPIC_CACHE", "_ANTHROPIC_"))
            for target in node.targets
        )
    ]
    namespace = {}
    exec(compile(ast.Module(body=constants + functions, type_ignores=[]),
                 str(MODULE), "exec"), namespace)
    return namespace

def breakpoints(plugin, messages):
    """Every level LiteLLM reads a breakpoint from, keyed by message index."""
    found = {}
    for index, message in enumerate(messages):
        if message.get("cache_control"):
            found[index] = "message"
        for call in message.get("tool_calls") or ():
            if isinstance(call, dict) and call.get("cache_control"):
                found[index] = "tool_call"
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("cache_control"):
                    found[index] = "block"
    return found


def conversation(turns):
    """A tool-driven conversation: user, then `turns` tool_call/result pairs."""
    messages = [{"role": "user", "content": "Read the deployment and summarise."}]
    for turn in range(turns):
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_{turn}",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"d.yaml"}'},
            }],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": f"call_{turn}",
            "content": "replicas: 2\nimage: litellm\n",
        })
    return messages


def test_anchors_follow_the_tool_tail():
    plugin = load_plugin()
    messages = conversation(turns=3)
    assert plugin["_apply_conversation_cache"](messages) == 2
    marked = breakpoints(plugin, messages)
    # The two trailing turns, not the head: index 0 must stay unmarked.
    assert sorted(marked) == [5, 6], marked
    assert marked[6] == "message", "tool result marks at message level"
    assert marked[5] == "tool_call", "assistant tool call marks inside the call"


def test_anchor_advances_as_the_conversation_grows():
    plugin = load_plugin()
    short, long = conversation(turns=1), conversation(turns=4)
    plugin["_apply_conversation_cache"](short)
    plugin["_apply_conversation_cache"](long)
    # A stalled window would mark the same early indices in both.
    assert max(breakpoints(plugin, long)) > max(breakpoints(plugin, short))


def test_server_tool_calls_are_not_anchors():
    plugin = load_plugin()
    messages = [
        {"role": "user", "content": "Search the web."},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "srvtoolu_01", "type": "function",
            "function": {"name": "web_search", "arguments": "{}"},
        }]},
    ]
    # LiteLLM emits server_tool_use without cache_control (factory.py:1971),
    # so the breakpoint must fall back to the user turn.
    plugin["_apply_conversation_cache"](messages)
    assert breakpoints(plugin, messages) == {0: "block"}


def test_reasoning_blocks_are_never_anchors():
    plugin = load_plugin()
    messages = [
        {"role": "user", "content": "Explain."},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Here it is."},
            {"type": "thinking", "thinking": "secret"},
        ]},
    ]
    plugin["_apply_conversation_cache"](messages)
    block = messages[1]["content"]
    assert block[0].get("cache_control"), "text block carries the breakpoint"
    assert not block[1].get("cache_control"), "thinking block must stay clean"


def test_breakpoint_count_sees_every_level():
    plugin = load_plugin()
    messages = conversation(turns=2)
    plugin["_apply_conversation_cache"](messages)
    # Two anchors, both outside plain content blocks.
    assert plugin["_count_cache_breakpoints"](messages) == 2


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
