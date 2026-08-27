import ast
import copy
import json
from pathlib import Path

source = Path(__file__).with_name("sitecustomize.py").read_text()
module = ast.parse(source)
names = {
    "_content_to_text",
    "_repair_codex_tool_pairs",
    "_messages_to_codex_input",
    "_tools_to_codex_tools",
    "_codex_tool_choice",
    "_codex_request_body",
    "_google_model_supports_function_ids",
    "_google_text_parts",
    "_google_tool_choice",
    "_tools_to_antigravity_tools",
    "_tool_result_value",
    "_messages_to_antigravity_payload",
    "_map_antigravity_model",
}
functions = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name in names]
namespace = {"json": json, "uuid": type("Uuid", (), {"uuid4": staticmethod(lambda: "request-id")})(), "_thought_signatures": {}}
exec(compile(ast.Module(body=functions, type_ignores=[]), "sitecustomize.py", "exec"), namespace)

codex = namespace["_codex_request_body"](
    "gpt-5.6-terra",
    [
        {"role": "assistant", "tool_calls": [{"id": "call-a", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "orphan", "content": "lost"},
    ],
    [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
    {"tool_choice": {"type": "function", "function": {"name": "read"}}, "reasoning_effort": "high", "max_tokens": 42},
)
assert codex["tool_choice"] == {"type": "function", "name": "read"}
assert codex["reasoning"]["context"] == "all_turns"
assert any(item["type"] == "message" and "orphan" in str(item["content"]) for item in codex["input"])

payload = namespace["_messages_to_antigravity_payload"](
    "gemini-3.7-flash",
    [
        {"role": "system", "content": "client instructions"},
        {"role": "assistant", "tool_calls": [
            {"id": "call-a|sig-a", "function": {"name": "read", "arguments": "{\"path\":\"x\"}"}},
            {"id": "call-b|sig-b", "function": {"name": "grep", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call-a|sig-a", "content": "ok"},
        {"role": "tool", "tool_call_id": "call-b|sig-b", "content": "bad", "is_error": True},
    ],
    "project",
    [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
    {"tool_choice": {"type": "function", "function": {"name": "read"}}, "max_tokens": 77},
)
request = payload["request"]
assert request["generationConfig"]["maxOutputTokens"] == 77
assert request["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == {"type": "object"}
assert request["toolConfig"]["functionCallingConfig"] == {"mode": "ANY", "allowedFunctionNames": ["read"]}
model_turn = request["contents"][0]
calls = [part["functionCall"] for part in model_turn["parts"] if "functionCall" in part]
assert [call["id"] for call in calls] == ["call-a", "call-b"]
response_turn = request["contents"][1]
responses = [part["functionResponse"] for part in response_turn["parts"]]
assert len(responses) == 2 and responses[0]["id"] == "call-a" and responses[1]["response"] == {"error": "bad"}
print("Codex and Antigravity wire contracts OK")

# Captured OMP CLI wire payloads (/tmp/omp-req-{codex,gemini}.json) carry
# max_completion_tokens, never max_tokens. Both bridges must honour that
# spelling or the client's output ceiling is silently ignored.
omp_extra = {"reasoning_effort": "high", "max_completion_tokens": 8192, "store": False}

omp_codex = namespace["_codex_request_body"](
    "gpt-5.6-terra",
    [{"role": "user", "content": "ping"}],
    None,
    dict(omp_extra),
)
assert "max_output_tokens" not in omp_codex
assert "max_completion_tokens" not in omp_codex
assert "max_tokens" not in omp_codex
assert omp_codex["reasoning"]["effort"] == "high"

omp_gemini = namespace["_messages_to_antigravity_payload"](
    "gemini-3.7-flash",
    [{"role": "user", "content": "ping"}],
    "project",
    None,
    dict(omp_extra),
)
assert omp_gemini["request"]["generationConfig"]["maxOutputTokens"] == 8192
print("OMP-shaped max_completion_tokens parity OK (Codex + Antigravity)")
