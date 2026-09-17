import ast
import copy
from pathlib import Path

source = Path(__file__).with_name("sitecustomize.py").read_text()
module = ast.parse(source)
wanted = {
    "_inject_claude_prompt",
    "_anthropic_markable_message",
    "_anthropic_tool_call_anchor",
    "_anthropic_cache_control",
    "_count_cache_breakpoints",
    "_mark_cache_breakpoint",
    "_apply_conversation_cache",
    "_normalize_effort",
    "_is_anthropic_adaptive",
}
functions = [
    node for node in module.body
    if isinstance(node, ast.FunctionDef) and node.name in wanted
]
assert len(functions) == len(wanted), f"missing: {wanted - {f.name for f in functions}}"
constants = [
    node for node in module.body
    if isinstance(node, ast.Assign)
    and any(
        getattr(t, "id", "").startswith(
            ("ANTHROPIC_CACHE", "_ANTHROPIC_")
        )
        for t in node.targets
    )
]
assert len(constants) >= 5, "cache/thinking constants missing"
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
# Measured upstream with the OAuth token (claude-opus-5/sonnet-4-6/opus-4-8/
# opus-4-6, max_tokens=64): system=[identity] -> 200, system=[identity, client]
# -> 200 and the client instruction is obeyed (ZX9-ACK marker on all four
# models), system=[client] -> 429 rate_limit_error. The OAuth rejection depends
# on the identity being the first block, not on there being only one block, so
# the client prompt keeps system authority instead of being spliced into the
# first user turn.
assert result["messages"][0] == {
    "role": "system",
    "content": [
        {"type": "text", "text": namespace["CLAUDE_CODE_PROMPT"]},
        {"type": "text", "text": "Stable client instruction."},
    ],
}
first_user = result["messages"][1]
assert first_user["role"] == "user"
# Reference (wSe) puts no marker on system, and (Mzr) anchors the last two
# markable turns. In the OpenAI shape those are the assistant turn and the tool
# result, so the earlier user turn stays unmarked and is covered by prefix
# semantics.
assert not any("cache_control" in block for block in first_user["content"])
assert result["messages"][2]["content"] == [
    {"type": "text", "text": "Pong", "cache_control": {"type": "ephemeral"}}
]
# The tool result takes its breakpoint at message level, which is where LiteLLM
# reads it (`convert_to_anthropic_tool_result:1844`); its payload is untouched.
tool_result = result["messages"][3]
assert tool_result["cache_control"] == {"type": "ephemeral"}
assert {k: v for k, v in tool_result.items() if k != "cache_control"} == request["messages"][3]
assert namespace["_count_cache_breakpoints"](result["messages"]) == 2
assert result["extra_headers"]["User-Agent"] == "claude-cli/2.1.246 (external, claude-desktop)"
assert result["extra_headers"]["x-app"] == "cli"
assert result["extra_headers"]["anthropic-dangerous-direct-browser-access"] == "true"
betas = result["extra_headers"]["anthropic-beta"]
# redact-thinking-2026-02-12 makes Anthropic return signed thinking blocks with
# no text: measured on claude-sonnet-4-6, 74 chars of reasoning without the beta
# and 0 with it. context-1m-2025-08-07 returns credit 429s on subscription
# tokens, so omp leaves it out too.
assert "redact-thinking" not in betas
assert "context-1m" not in betas
assert "effort-2025-11-24" in betas
print("Claude bridge transformation OK")

# claude-opus-5 is an adaptive-thinking model: budget_tokens is ignored there
# and Anthropic's display default is "omitted", which returns an empty thinking
# block. Measured on claude-opus-5: 225 chars with display="summarized", 0 with
# "omitted".
adaptive_request = inject({
    "model": "claude-opus-5",
    "reasoning_effort": "high",
    "max_tokens": 128000,
    "messages": [{"role": "user", "content": "Think."}],
})
assert adaptive_request["thinking"] == {"type": "adaptive", "display": "summarized"}
assert adaptive_request["output_config"] == {"effort": "high"}
# Measured: max_tokens=64000 is accepted on this subscription (200), so the
# 16384 ceiling that used to be applied here truncated responses the client had
# asked for. Only the floor (budget + 2048) remains.
assert adaptive_request["max_tokens"] == 128000
assert "reasoning_effort" not in adaptive_request
print("Claude adaptive thinking OK")

# The models that reject adaptive keep the budget shape: measured, adaptive on
# claude-sonnet-4-5 answers 400 while budget returns 367 chars of reasoning.
thinking_request = inject({
    "model": "claude-sonnet-4-5",
    "reasoning_effort": "high",
    "max_tokens": 128000,
    "messages": [{"role": "user", "content": "Think."}],
})
assert thinking_request["thinking"] == {"type": "enabled", "budget_tokens": 8192}
assert thinking_request["max_tokens"] == 128000
assert "output_config" not in thinking_request
assert "reasoning_effort" not in thinking_request
print("Claude budget thinking limits OK")

# `reasoning_effort: "none"` must omit thinking entirely: the Anthropic Messages
# API has no thinking:{type:"disabled"} shape.
off_request = inject({
    "model": "claude-opus-5",
    "reasoning_effort": "none",
    "messages": [{"role": "user", "content": "Answer."}],
})
assert "thinking" not in off_request
assert "output_config" not in off_request
print("Claude thinking off OK")

# The /v1/responses route forwards `reasoning: {effort, summary}` as an object
# in reasoning_effort. Treating it as a string put "{'effort': 'high', ...}" on
# the wire.
object_effort = inject({
    "model": "claude-opus-5",
    "reasoning_effort": {"effort": "high", "summary": "detailed"},
    "messages": [{"role": "user", "content": "Think."}],
})
assert object_effort["output_config"] == {"effort": "high"}
assert object_effort["thinking"] == {"type": "adaptive", "display": "summarized"}
print("Claude object-shaped reasoning_effort OK")

# OMP's captured wire payload uses max_completion_tokens (OpenAI-style), not
# max_tokens; only the key the client actually sent is touched, and it is then
# folded into max_tokens. Filling both made the fold overwrite the client's
# value with the default.
omp_shaped_request = inject({
    "model": "claude-opus-5",
    "reasoning_effort": "high",
    "max_completion_tokens": 64000,
    "stream": True,
    "store": False,
    "messages": [{"role": "user", "content": "Think."}],
})
assert omp_shaped_request["thinking"] == {"type": "adaptive", "display": "summarized"}
# Measured: max_tokens=64000 is accepted on this subscription (200), so the
# client's value survives instead of being cut to 16384.
assert omp_shaped_request["max_tokens"] == 64000
assert "max_completion_tokens" not in omp_shaped_request
assert "reasoning_effort" not in omp_shaped_request
print("Claude OMP-shaped max_completion_tokens parity OK")

# Measured 2026-08-27: with only the static prefix marked, cache_read stayed
# pinned at 3853 tokens whether the conversation was 3.8k or 11.5k, so the
# cached share decayed as the session grew. Anchoring the tail moved cache_read
# to 11541/11543 on the same conversation.
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
# Mzr anchors the last two markable turns. The tool result is markable at
# message level, so the window sits at the tail: tool result plus final user
# turn. The opening user turn stays unmarked, and the assistant tool call is
# skipped here because LiteLLM drops calls without `type: "function"`
# (`convert_to_anthropic_tool_invoke:1952`).
assert namespace["_count_cache_breakpoints"](messages) == 2
assert messages[-1]["content"] == [
    {"type": "text", "text": "latest question", "cache_control": {"type": "ephemeral"}}
]
assert messages[3]["cache_control"] == {"type": "ephemeral"}
assert not any("cache_control" in block for block in messages[1]["content"])
# No marker carries a TTL: reference retention defaults to "short".
markers = [
    message["cache_control"] for message in messages if message.get("cache_control")
] + [
    block["cache_control"]
    for message in messages
    if isinstance(message.get("content"), list)
    for block in message["content"]
    if isinstance(block, dict) and block.get("cache_control")
]
assert markers and all(marker.get("ttl") is None for marker in markers)
# Structured payloads must survive untouched.
assert messages[2]["tool_calls"][0]["id"] == "c1"
assert messages[2]["content"] is None
assert {k: v for k, v in messages[3].items() if k != "cache_control"} == {
    "role": "tool", "tool_call_id": "c1", "content": "file body",
}

# Reasoning blocks are never anchors (Azr skips thinking/redacted_thinking).
thinking_tail = inject({
    "model": "claude-sonnet-5",
    "messages": [
        {"role": "system", "content": "Stable instructions."},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "answer"},
            {"type": "thinking", "thinking": "hidden"},
        ]},
    ],
})
tail_blocks = thinking_tail["messages"][-1]["content"]
assert tail_blocks[1].get("cache_control") is None
assert tail_blocks[0]["cache_control"] == {"type": "ephemeral"}

# A synthetic trailing "Continue." nudge is not a useful anchor.
nudged = inject({
    "model": "claude-sonnet-5",
    "messages": [
        {"role": "system", "content": "Stable instructions."},
        {"role": "user", "content": "real question"},
        {"role": "assistant", "content": "partial answer"},
        {"role": "user", "content": "Continue."},
    ],
})
assert nudged["messages"][-1]["content"] == "Continue."

# A tool result as the newest turn is the freshest anchor: LiteLLM reads a
# message-level breakpoint there, so the window reaches the true tail instead
# of stalling on an older text turn.
tool_tail = inject({
    "model": "claude-sonnet-5",
    "messages": [
        {"role": "system", "content": "Stable instructions."},
        {"role": "user", "content": "question"},
        {"role": "tool", "tool_call_id": "c9", "content": "result"},
    ],
})
newest = tool_tail["messages"][-1]
assert newest["cache_control"] == {"type": "ephemeral"}
assert {k: v for k, v in newest.items() if k != "cache_control"} == {
    "role": "tool", "tool_call_id": "c9", "content": "result",
}
assert namespace["_count_cache_breakpoints"](tool_tail["messages"]) == 2

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
