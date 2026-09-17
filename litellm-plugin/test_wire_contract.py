import ast
import base64
import copy
import hashlib
import json
import mimetypes
import re
import sys
import time
import urllib.parse as _url
from pathlib import Path

source = Path(__file__).with_name("sitecustomize.py").read_text()
module = ast.parse(source)
names = {
    "_content_to_text",
    "_content_to_codex_parts",
    "_codex_image_part",
    "_codex_file_part",
    "_codex_composite_call_id",
    "_codex_split_call_id",
    "_codex_wire_generation",
    "_codex_prompt_cache_key",
    "_normalize_effort",
    "_repair_codex_tool_pairs",
    "_messages_to_codex_input",
    "_tools_to_codex_tools",
    "_codex_tool_choice",
    "_codex_request_body",
    "_codex_remember_unsupported",
    "_google_model_supports_function_ids",
    "_google_inline_part",
    "_google_media_from_url",
    "_google_media_part",
    "_google_content_parts",
    "_google_tool_choice",
    "_google_is_planning_leak",
    "_google_is_flash_leak_model",
    "_tools_to_antigravity_tools",
    "_tool_result_value",
    "_messages_to_antigravity_payload",
    "_antigravity_base_family",
    "_map_antigravity_model",
    "_antigravity_request_id",
}
functions = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name in names]
assert len(functions) == len(names), f"missing: {names - {f.name for f in functions}}"
constants = [
    node for node in module.body
    if isinstance(node, ast.Assign)
    and any(
        getattr(t, "id", "").startswith(("_CODEX_", "_GOOGLE_", "_ANTIGRAVITY_", "_antigravity_"))
        for t in node.targets
    )
]
namespace = {
    "json": json,
    "base64": base64,
    "mimetypes": mimetypes,
    "re": re,
    "_url": _url,
    "hashlib": hashlib,
    "time": time,
    "uuid": type("Uuid", (), {
        "uuid4": staticmethod(lambda: type("U", (), {
            "hex": "requestid",
            "__str__": lambda self: "request-id",
        })())
    })(),
    "sys": sys,
    "_thought_signatures": {},
    "_remember_thought_signature": lambda call_id, signature: None,
    # Only the timeout constants touch httpx at import time; the wire builders
    # under test never open a connection.
    "httpx": type("Httpx", (), {"Timeout": staticmethod(lambda *a, **k: None)})(),
    "os": type("Os", (), {"environ": {}})(),
}
exec(compile(ast.Module(body=constants + functions, type_ignores=[]), "sitecustomize.py", "exec"), namespace)

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


# --- Codex payload: multimodal, composite ids, hosted tools, juice ---------
# Before this, _content_to_text dropped everything that was not text, so a
# request carrying an image reached the model without the image.
PNG = "data:image/png;base64,iVBORw0KGgo="
multimodal = namespace["_codex_request_body"](
    "gpt-5.5",
    [{"role": "user", "content": [
        {"type": "text", "text": "what colour?"},
        {"type": "image_url", "image_url": {"url": PNG, "detail": "original"}},
        {"type": "file", "file": {"filename": "a.txt", "file_data": "data:text/plain;base64,aGk="}},
    ]}],
    None,
    None,
)
parts = multimodal["input"][0]["content"]
assert [part["type"] for part in parts] == ["input_text", "input_image", "input_file"]
# Codex rejects detail "original".
assert parts[1]["detail"] == "auto"
assert parts[2]["filename"] == "a.txt"
print("Codex multimodal input OK")

# Responses identifies a call by (call_id, item_id); the composite id keeps the
# pair addressable and the replay must send the bare call_id back.
composite = namespace["_codex_composite_call_id"]("call_a", "fc_1")
assert composite == "call_a|fc_1"
assert namespace["_codex_split_call_id"](composite) == "call_a"
replay = namespace["_codex_request_body"](
    "gpt-5.5",
    [
        {"role": "assistant", "tool_calls": [{"id": composite, "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": composite, "content": "ok"},
    ],
    None,
    None,
)
assert [item.get("call_id") for item in replay["input"] if item.get("call_id")] == ["call_a", "call_a"]
print("Codex composite tool-call ids OK")

# Hosted tools carry no `function` and were being dropped.
hosted = namespace["_codex_request_body"](
    "gpt-5.6-terra",
    [{"role": "user", "content": "search"}],
    [{"type": "web_search"}, {"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
    {"reasoning_effort": "none"},
)
assert [tool["type"] for tool in hosted["tools"]] == ["web_search", "function"]
# Reasoning off on 5.6+ pins the juice instead of sending `reasoning`.
assert "reasoning" not in hosted
assert "# Juice: 0 !important" in json.dumps(hosted["input"])
older = namespace["_codex_request_body"]("gpt-5.5", [{"role": "user", "content": "x"}], None, {"reasoning_effort": "none"})
assert "Juice" not in json.dumps(older["input"])
print("Codex hosted tools and juice item OK")

# The prompt cache key must stay identical as the conversation grows, or the
# backend never reuses the cached prefix.
head = [{"role": "system", "content": "rules"}, {"role": "user", "content": "first"}]
assert namespace["_codex_prompt_cache_key"](head) == namespace["_codex_prompt_cache_key"](
    head + [{"role": "assistant", "content": "reply"}, {"role": "user", "content": "second"}]
)
print("Codex prompt cache key stability OK")

# Remapping an arbitrary name to the fallback model answered 200 while the
# `model` field echoed the requested name: billing and comparisons lied. Only
# deliberate aliases may be remapped.
for alias in ("codex", "gpt-6"):
    assert namespace["_codex_remember_unsupported"]({"model": alias}) is True, alias
# A family alias ("codex", "gpt-5", "gpt-6") promises no concrete version, so
# resolving it to the served one is honest. `gpt-5.4`/`gpt-5.4-mini` name a
# version this account does not serve: remapping them to gpt-5.5 billed and
# logged the client against a model that never ran, so they are no longer
# aliases and the upstream refusal propagates.
for arbitrary in ("gpt-5.4", "gpt-5.4-mini", "gpt-4.1", "o3-mini", "gpt-3.5-turbo"):
    assert namespace["_codex_remember_unsupported"]({"model": arbitrary}) is False, arbitrary
print("Codex honest model fallback OK")

# --- Antigravity: thinking level, unknown families, planning leak ----------
# gemini-3.8-* and gemini-3.1-pro answer 400 "Thinking level MINIMAL is not
# supported for this model", so MINIMAL must never reach the wire.
for effort in ("none", "minimal", "low", "medium", "high", "max"):
    config = namespace["_messages_to_antigravity_payload"](
        "gemini-3.8-flash", [{"role": "user", "content": "x"}], "project", None,
        {"reasoning_effort": effort},
    )["request"]["generationConfig"]["thinkingConfig"]
    assert config.get("thinkingLevel") != "MINIMAL", effort
    assert config["includeThoughts"] is (effort != "none"), effort
print("Antigravity thinking level clamp OK")

# The gemini-* wildcard used to make any invented name answer as
# gemini-2.5-flash with the requested name echoed back.
try:
    namespace["_map_antigravity_model"]("gemini-9.9-ultra", "high")
except Exception as error:
    assert "not served by this account" in str(error)
else:  # pragma: no cover
    raise AssertionError("an unknown gemini family must not fall back silently")
assert namespace["_antigravity_base_family"]("gemini-3.8-flash-high") == "gemini-3.8-flash"
print("Antigravity unknown family fails loudly OK")

# Flash models leak their planning object into visible text; a legitimate JSON
# answer must survive.
leak = namespace["_google_is_planning_leak"]
assert leak('{"thought":"planning","_i":3}')
assert leak('{"thought":"only"}')
assert not leak('{"result": 42, "ok": true}')
assert not leak("plain text")
# omp consumePlanningBuffer classifies as a leak any object carrying `thought`,
# `call`, `_i`, `paths`, `command`, or the `path`+`content` pair. Requiring
# `thought` let a leak shaped {"call":...,"_i":...} through whole to the client.
assert leak('{"call":"f"}')
assert leak('{"path":"a.py","content":"x"}')
# Only the flash family spills planning into the visible text
# (isFlashLeakModel); applied to every model, a `pro` legitimately answering
# {"command": "ls"} saw its answer erased.
assert leak('{"command":"ls"}', "gemini-3.8-flash-low")
assert not leak('{"command":"ls"}', "gemini-3.1-pro-low")
print("Antigravity planning leak filter OK")

# The Responses route sends reasoning as an object.
assert namespace["_normalize_effort"]({"effort": "high", "summary": "detailed"}) == ("high", "detailed")
assert namespace["_normalize_effort"]("low") == ("low", None)
assert namespace["_normalize_effort"](None) == (None, None)
print("Reasoning effort normalisation OK")
