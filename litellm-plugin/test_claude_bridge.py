import ast
import copy
from pathlib import Path

source = Path(__file__).with_name("sitecustomize.py").read_text()
module = ast.parse(source)
function = next(
    node for node in module.body
    if isinstance(node, ast.FunctionDef) and node.name == "_inject_claude_prompt"
)
namespace = {
    "CLAUDE_CODE_PROMPT": "You are a Claude agent, built on Anthropic's Claude Agent SDK.",
    "_token_manager": type("TokenManager", (), {"get_anthropic_token": lambda self: "token"})(),
    "os": type("Os", (), {"environ": {}})(),
}
exec(compile(ast.Module(body=[function], type_ignores=[]), "sitecustomize.py", "exec"), namespace)
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
assert first_user["content"][0]["cache_control"] == {"type": "ephemeral"}
assert first_user["content"][1] == {"type": "text", "text": "Ping"}
assert result["messages"][2:] == request["messages"][2:]
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
