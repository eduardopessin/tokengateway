import ast
import copy
from pathlib import Path

source = Path(__file__).with_name("sitecustomize.py").read_text()
module = ast.parse(source)
wanted = {
    "_inject_claude_prompt",
    "_is_cacheable_text_message",
    "_count_cache_breakpoints",
    "_mark_cache_breakpoint",
}
functions = [
    node for node in module.body
    if isinstance(node, ast.FunctionDef) and node.name in wanted
]
assert len(functions) == len(wanted), f"missing: {wanted - {f.name for f in functions}}"
constants = [
    node for node in module.body
    if isinstance(node, ast.Assign)
    and any(getattr(t, "id", None) == "ANTHROPIC_MAX_CACHE_BREAKPOINTS" for t in node.targets)
]
assert constants, "ANTHROPIC_MAX_CACHE_BREAKPOINTS missing"
namespace = {
    "CLAUDE_CODE_PROMPT": "You are a Claude agent, built on Anthropic's Claude Agent SDK.",
    "_token_manager": type("TokenManager", (), {"get_anthropic_token": lambda self: "token"})(),
    "os": type("Os", (), {"environ": {}})(),
}
exec(compile(ast.Module(body=constants + functions, type_ignores=[]), "sitecustomize.py", "exec"), namespace)
inject = namespace["_inject_claude_prompt"]

request = {
    "model": "claude-opus-5",
    "messages": [
        {"role": "system", "content": "Stable client instruction."},
        {"role": "user", "content": [{"type": "text", "text": "Ping"}]},
        {"role": "assistant", "content": "Pong"},
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
    ],
}
result = inject(copy.deepcopy(request))
assert result["messages"][0] == {
    "role": "system",
    "content": [{"type": "text", "text": namespace["CLAUDE_CODE_PROMPT"]}],
}
first_user = result["messages"][1]
assert first_user["role"] == "user"
assert first_user["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
assert first_user["content"][1] == {"type": "text", "text": "Ping"}
# The assistant turn is the newest safe text block, so it carries the rolling
# breakpoint; the structured tool result must stay byte-identical.
assert result["messages"][2]["content"] == [
    {"type": "text", "text": "Pong", "cache_control": {"type": "ephemeral"}}
]
assert result["messages"][3] == request["messages"][3]
assert result["extra_headers"]["User-Agent"] == "claude-cli/2.1.246 (external, claude-desktop)"
print("Claude bridge transformation OK")

thinking_request = inject({
    "model": "claude-opus-5",
    "reasoning_effort": "high",
    "max_tokens": 128000,
    "messages": [{"role": "user", "content": "Think."}],
})
assert thinking_request["thinking"] == {"type": "enabled", "budget_tokens": 8192}
assert thinking_request["max_tokens"] == 16384
assert "reasoning_effort" not in thinking_request
print("Claude thinking limits OK")

# OMP's captured wire payload uses max_completion_tokens (OpenAI-style), not
# max_tokens; the clamp must fold it into max_tokens or the raw high value
# still reaches Anthropic and reproduces the 2026-08-27 429 regression.
omp_shaped_request = inject({
    "model": "claude-opus-5",
    "reasoning_effort": "high",
    "max_completion_tokens": 64000,
    "stream": True,
    "store": False,
    "messages": [{"role": "user", "content": "Think."}],
})
assert omp_shaped_request["thinking"] == {"type": "enabled", "budget_tokens": 8192}
assert omp_shaped_request["max_tokens"] == 16384
assert "max_completion_tokens" not in omp_shaped_request
assert "reasoning_effort" not in omp_shaped_request
print("Claude OMP-shaped max_completion_tokens parity OK")

# Measured 2026-08-27: with only the static prefix marked, cache_read stayed
# pinned at 3853 tokens whether the conversation was 3.8k or 11.5k, so the
# cached share decayed as the session grew. A rolling tail breakpoint moved
# cache_read to 11526/11543 on the same conversation.
agent_turn = inject({
    "model": "claude-sonnet-5",
    "messages": [
        {"role": "system", "content": "Stable instructions."},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
        {"role": "user", "content": "latest question"},
    ],
})
messages = agent_turn["messages"]
breakpoints = namespace["_count_cache_breakpoints"](messages)
assert breakpoints == 2, breakpoints
# Static prefix keeps the long TTL; the rolling tail uses the 5m default
# because it is rewritten every turn.
assert messages[1]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
assert messages[-1]["content"] == [
    {"type": "text", "text": "latest question", "cache_control": {"type": "ephemeral"}}
]
# Structured payloads must survive untouched.
assert messages[2]["tool_calls"][0]["id"] == "c1"
assert messages[2]["content"] is None
assert messages[3] == {"role": "tool", "tool_call_id": "c1", "content": "file body"}

# A tool result as the newest turn is not a candidate: the marker would corrupt
# the schema, so the tail falls back to the newest plain-text message.
tool_tail = inject({
    "model": "claude-sonnet-5",
    "messages": [
        {"role": "system", "content": "Stable instructions."},
        {"role": "user", "content": "question"},
        {"role": "tool", "tool_call_id": "c9", "content": "result"},
    ],
})
assert tool_tail["messages"][-1] == {"role": "tool", "tool_call_id": "c9", "content": "result"}
assert namespace["_count_cache_breakpoints"](tool_tail["messages"]) == 1

# Single user turn: the static marker already covers it, so no duplicate.
single = inject({
    "model": "claude-sonnet-5",
    "messages": [
        {"role": "system", "content": "Stable instructions."},
        {"role": "user", "content": "only turn"},
    ],
})
assert namespace["_count_cache_breakpoints"](single["messages"]) == 1

# Never exceed Anthropic's ceiling when the client already sent its own markers.
saturated = inject({
    "model": "claude-sonnet-5",
    "messages": [
        {"role": "system", "content": "Stable instructions."},
        {"role": "user", "content": [
            {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "b", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "c", "cache_control": {"type": "ephemeral"}},
        ]},
        {"role": "assistant", "content": "tail"},
    ],
})
assert namespace["_count_cache_breakpoints"](saturated["messages"]) <= 4
print("Claude multi-turn cache breakpoints OK")
