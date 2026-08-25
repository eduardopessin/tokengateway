import functools
import sys
import os
import json
import time
import uuid
import asyncio
import base64
import ssl
import urllib.request
import urllib.parse
import threading
import httpx

os.environ.setdefault("GEMINI_API_KEY", "dummy-antigravity")
os.environ.setdefault("GOOGLE_API_KEY", "dummy-antigravity")

# --- 1. Patch MCP spec-server (ArgoCD / Gitea) ---
try:
    from litellm.proxy._experimental.mcp_server import mcp_server_manager as _m

    _Mgr = _m.MCPServerManager

    def _wrap(name):
        orig = getattr(_Mgr, name)

        @functools.wraps(orig)
        async def wrapper(self, server, *args, **kwargs):
            if getattr(server, "spec_path", None):
                return []
            return await orig(self, server, *args, **kwargs)

        setattr(_Mgr, name, wrapper)

    _patched = []
    for _n in (
        "get_resources_from_server",
        "get_prompts_from_server",
        "get_resource_templates_from_server",
    ):
        if hasattr(_Mgr, _n):
            _wrap(_n)
            _patched.append(_n)
    print(f"[sitecustomize] LiteLLM MCP spec-server patch applied: {_patched}", file=sys.stderr)
except Exception as _e:
    print(f"[sitecustomize] LiteLLM MCP patch skipped: {_e}", file=sys.stderr)

# --- helper: atomically persist tokens across Kubernetes Secrets ---
def _persist_tokens_to_secret(updates: dict):
    try:
        sa_token = open('/var/run/secrets/kubernetes.io/serviceaccount/token').read()
        ca = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
        ns = open('/var/run/secrets/kubernetes.io/serviceaccount/namespace').read().strip()
        host = os.environ.get('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')
        port = os.environ.get('KUBERNETES_SERVICE_PORT', '443')
        patch = {k: base64.b64encode(v.encode()).decode() for k, v in updates.items() if v}
        if not patch:
            return
        body = json.dumps({'data': patch}).encode()
        req = urllib.request.Request(
            f'https://{host}:{port}/api/v1/namespaces/{ns}/secrets/litellm-secrets',
            data=body, method='PATCH',
            headers={
                'Authorization': f'Bearer {sa_token}',
                'Content-Type': 'application/strategic-merge-patch+json'
            }
        )
        ctx = ssl.create_default_context(cafile=ca)
        urllib.request.urlopen(req, context=ctx, timeout=5)
        print(f"[TokenManager] litellm-secrets persisted: {list(updates.keys())}", file=sys.stderr)
    except Exception as e:
        print(f"[TokenManager] Warning: failed to persist secret: {e}", file=sys.stderr)

# --- 2. In-Memory Token Manager with Auto-Refresh ---
ANTHROPIC_CLIENT_ID = os.environ.get("ANTHROPIC_CLIENT_ID", "")
CODEX_CLIENT_ID = os.environ.get("CODEX_CLIENT_ID", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

class TokenManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._anthropic_token = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
        self._anthropic_refresh = os.environ.get("ANTHROPIC_REFRESH_TOKEN", "")
        self._codex_token = os.environ.get("OPENAI_CODEX_OAUTH_TOKEN", "")
        self._codex_refresh = os.environ.get("OPENAI_CODEX_REFRESH_TOKEN", "")
        self._google_token = os.environ.get("GOOGLE_ANTIGRAVITY_OAUTH_TOKEN", "")
        self._google_refresh = os.environ.get("GOOGLE_ANTIGRAVITY_REFRESH_TOKEN", "")
        self._google_project_id = os.environ.get("GOOGLE_ANTIGRAVITY_PROJECT_ID", "")
        self._last_refresh = {
            "anthropic": 0,
            "codex": 0,
            "google": 0
        }

    def get_anthropic_token(self, force_refresh=False):
        with self._lock:
            now = time.time()
            if not self._anthropic_token or force_refresh or (now - self._last_refresh["anthropic"] > 3600):
                if self._anthropic_refresh:
                    try:
                        data = json.dumps({
                            "grant_type": "refresh_token",
                            "client_id": ANTHROPIC_CLIENT_ID,
                            "refresh_token": self._anthropic_refresh
                        }).encode("utf-8")
                        req = urllib.request.Request(
                            "https://api.anthropic.com/v1/oauth/token",
                            data=data,
                            headers={"Content-Type": "application/json"}
                        )
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            res = json.loads(resp.read().decode("utf-8"))
                            self._anthropic_token = res.get("access_token", self._anthropic_token)
                            self._anthropic_refresh = res.get("refresh_token", self._anthropic_refresh)
                            self._last_refresh["anthropic"] = now
                            print("[TokenManager] Anthropic token refreshed com sucesso", file=sys.stderr)
                            _persist_tokens_to_secret({
                                "ANTHROPIC_OAUTH_TOKEN": self._anthropic_token,
                                "ANTHROPIC_REFRESH_TOKEN": self._anthropic_refresh
                            })
                    except Exception as e:
                        print(f"[TokenManager] Erro ao renovar Anthropic token: {e}", file=sys.stderr)
            return self._anthropic_token

    def get_codex_token(self, force_refresh=False):
        with self._lock:
            now = time.time()
            if not self._codex_token or force_refresh or (now - self._last_refresh["codex"] > 3600):
                if self._codex_refresh:
                    try:
                        data = json.dumps({
                            "grant_type": "refresh_token",
                            "client_id": CODEX_CLIENT_ID,
                            "refresh_token": self._codex_refresh
                        }).encode("utf-8")
                        req = urllib.request.Request(
                            "https://auth.openai.com/oauth/token",
                            data=data,
                            headers={"Content-Type": "application/json"}
                        )
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            res = json.loads(resp.read().decode("utf-8"))
                            self._codex_token = res.get("access_token", self._codex_token)
                            self._codex_refresh = res.get("refresh_token", self._codex_refresh)
                            self._last_refresh["codex"] = now
                            print("[TokenManager] OpenAI Codex token refreshed com sucesso", file=sys.stderr)
                            _persist_tokens_to_secret({
                                "OPENAI_CODEX_OAUTH_TOKEN": self._codex_token,
                                "OPENAI_CODEX_REFRESH_TOKEN": self._codex_refresh
                            })
                    except Exception as e:
                        print(f"[TokenManager] Erro ao renovar Codex token: {e}", file=sys.stderr)
            return self._codex_token

    def get_google_token(self, force_refresh=False):
        with self._lock:
            now = time.time()
            if not self._google_token or force_refresh or (now - self._last_refresh["google"] > 3600):
                if self._google_refresh:
                    try:
                        data = urllib.parse.urlencode({
                            "grant_type": "refresh_token",
                            "client_id": GOOGLE_CLIENT_ID,
                            "client_secret": GOOGLE_CLIENT_SECRET,
                            "refresh_token": self._google_refresh
                        }).encode("utf-8")
                        req = urllib.request.Request(
                            "https://oauth2.googleapis.com/token",
                            data=data,
                            headers={"Content-Type": "application/x-www-form-urlencoded"}
                        )
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            res = json.loads(resp.read().decode("utf-8"))
                            self._google_token = res.get("access_token", self._google_token)
                            self._last_refresh["google"] = now
                            print("[TokenManager] Google Antigravity token refreshed com sucesso", file=sys.stderr)
                            _persist_tokens_to_secret({
                                "GOOGLE_ANTIGRAVITY_OAUTH_TOKEN": self._google_token
                            })
                    except Exception as e:
                        print(f"[TokenManager] Error refreshing Google token: {e}", file=sys.stderr)
            return self._google_token

    def get_google_project_id(self):
        return self._google_project_id

_token_manager = TokenManager()

# --- 3. Wire protocol adapters for Claude Code, OpenAI Codex, and Google Antigravity ---
CLAUDE_CODE_PROMPT = "You are Claude Code, Anthropic's official CLI for Claude."

def _inject_claude_prompt(kwargs, args=None):
    model = str(kwargs.get("model", "") or (args[0] if args and len(args) > 0 else "")).lower()
    if "claude" in model or "anthropic" in model:
        fresh_anthropic_token = _token_manager.get_anthropic_token()
        if fresh_anthropic_token:
            kwargs["api_key"] = fresh_anthropic_token
            os.environ["ANTHROPIC_OAUTH_TOKEN"] = fresh_anthropic_token

        # Fix Anthropic extended thinking temperature rule:
        temp = kwargs.get("temperature")
        if temp is not None and float(temp) != 1.0:
            if kwargs.get("thinking"):
                kwargs["temperature"] = 1.0
            else:
                kwargs.pop("reasoning_effort", None)
                kwargs.pop("thinking", None)

        messages = kwargs.get("messages") or (args[1] if args and len(args) > 1 else [])
        if isinstance(messages, list):
            has_prompt = False
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "system":
                    c = m.get("content", "")
                    if isinstance(c, str) and CLAUDE_CODE_PROMPT in c:
                        has_prompt = True
                        break
                    elif isinstance(c, list):
                        for item in c:
                            if isinstance(item, dict) and CLAUDE_CODE_PROMPT in str(item.get("text", "")):
                                has_prompt = True
                                break
            if not has_prompt:
                kwargs["messages"] = [{"role": "system", "content": CLAUDE_CODE_PROMPT}] + list(messages)
    return kwargs

# --- 3.1. OpenAI Codex Bridge ---
def _is_codex_model(model_str):
    m = str(model_str).lower()
    return "gpt-5" in m or "codex" in m

def _messages_to_codex_input(messages):
    codex_input = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        
        # Handle tool results (role == "tool")
        if role == "tool":
            tool_call_id = m.get("tool_call_id") or ""
            text_out = str(content) if not isinstance(content, list) else "".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in ["text", "input_text"]
            )
            codex_input.append({
                "type": "function_call_output",
                "call_id": tool_call_id,
                "output": text_out
            })
            continue

        # Extract text content
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") in ["text", "input_text", "output_text"]:
                        text_parts.append(part.get("text", ""))
            content_text = "".join(text_parts)
        else:
            content_text = str(content) if content is not None else ""

        codex_role = "developer" if role == "system" else role
        if codex_role not in ["user", "assistant", "developer"]:
            codex_role = "user"

        content_type = "output_text" if codex_role == "assistant" else "input_text"

        if content_text:
            codex_input.append({
                "type": "message",
                "role": codex_role,
                "content": [{"type": content_type, "text": content_text}]
            })

        # Handle tool calls in assistant messages
        if m.get("tool_calls"):
            for tc in m.get("tool_calls", []):
                func = tc.get("function") or {}
                call_id = tc.get("id") or ""
                fn_name = func.get("name") or ""
                fn_args = func.get("arguments") or "{}"
                if isinstance(fn_args, dict):
                    fn_args = json.dumps(fn_args)
                codex_input.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": fn_name,
                    "arguments": fn_args
                })
    return codex_input

def _tools_to_codex_tools(tools):
    if not tools:
        return None
    codex_tools = []
    for t in tools:
        if isinstance(t, dict):
            if t.get("type") == "function" and "function" in t:
                fn = t["function"]
                codex_tools.append({
                    "type": "function",
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {})
                })
            elif "name" in t:
                codex_tools.append({
                    "type": "function",
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {})
                })
    return codex_tools if codex_tools else None

def _call_codex_sync(model, messages, token, tools=None):
    url = "https://chatgpt.com/backend-api/codex/responses"
    codex_input = _messages_to_codex_input(messages)
    codex_tools = _tools_to_codex_tools(tools)
    bare_model = model.split("/")[-1]
    
    body = {
        "model": bare_model,
        "store": False,
        "stream": True,
        "input": codex_input
    }
    if codex_tools:
        body["tools"] = codex_tools
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accept": "text/event-stream",
        "originator": "pi"
    }
    
    full_text = []
    tool_calls = []
    active_tools = {}

    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        resp = client.send(client.build_request("POST", url, json=body, headers=headers), stream=True)
        if resp.status_code == 401:
            resp.close()
            fresh_token = _token_manager.get_codex_token(force_refresh=True)
            if fresh_token:
                headers["Authorization"] = f"Bearer {fresh_token}"
                resp = client.send(client.build_request("POST", url, json=body, headers=headers), stream=True)
            else:
                resp.raise_for_status()

        if resp.status_code != 200:
            err_text = resp.read().decode("utf-8", "replace")
            resp.close()
            raise Exception(f"OpenAI Codex error {resp.status_code}: {err_text}")

        for line in resp.iter_lines():
            line_str = line.strip()
            if line_str.startswith("data: "):
                data_str = line_str[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                    etype = event.get("type")
                    if etype == "response.output_item.added":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                            active_tools[item.get("id")] = {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": item.get("name", ""), "arguments": ""}
                            }
                    elif etype == "response.function_call_arguments.delta":
                        item_id = event.get("item_id")
                        if item_id in active_tools:
                            active_tools[item_id]["function"]["arguments"] += event.get("delta", "")
                    elif etype == "response.output_item.done":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            item_id = item.get("id")
                            if item_id in active_tools:
                                if item.get("arguments"):
                                    active_tools[item_id]["function"]["arguments"] = item.get("arguments")
                                tool_calls.append(active_tools.pop(item_id))
                    elif etype == "response.output_text.delta":
                        full_text.append(event.get("delta", ""))
                except:
                    pass
        resp.close()

    return "".join(full_text), tool_calls

async def _stream_codex_generator(model, messages, token, tools=None):
    from litellm import ModelResponse
    url = "https://chatgpt.com/backend-api/codex/responses"
    codex_input = _messages_to_codex_input(messages)
    codex_tools = _tools_to_codex_tools(tools)
    bare_model = model.split("/")[-1]
    
    body = {
        "model": bare_model,
        "store": False,
        "stream": True,
        "input": codex_input
    }
    if codex_tools:
        body["tools"] = codex_tools
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accept": "text/event-stream",
        "originator": "pi"
    }
    
    resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    
    current_tool_index = 0
    active_tool_map = {}
    has_tool_calls = False

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        resp = await client.send(client.build_request("POST", url, json=body, headers=headers), stream=True)
        if resp.status_code == 401:
            await resp.aclose()
            fresh_token = _token_manager.get_codex_token(force_refresh=True)
            if fresh_token:
                headers["Authorization"] = f"Bearer {fresh_token}"
                resp = await client.send(client.build_request("POST", url, json=body, headers=headers), stream=True)
            else:
                resp.raise_for_status()

        if resp.status_code != 200:
            err_text = (await resp.aread()).decode("utf-8", "replace")
            await resp.aclose()
            raise Exception(f"OpenAI Codex error {resp.status_code}: {err_text}")

        async for line in resp.aiter_lines():
            line_str = line.strip()
            if line_str.startswith("data: "):
                data_str = line_str[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                    etype = event.get("type")

                    if etype == "response.output_item.added":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            has_tool_calls = True
                            call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                            fn_name = item.get("name", "")
                            active_tool_map[item.get("id")] = (current_tool_index, call_id, fn_name)
                            yield ModelResponse(
                                id=resp_id,
                                object="chat.completion.chunk",
                                created=created,
                                model=model,
                                choices=[{
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "tool_calls": [{
                                            "index": current_tool_index,
                                            "id": call_id,
                                            "type": "function",
                                            "function": {
                                                "name": fn_name,
                                                "arguments": ""
                                            }
                                        }]
                                    },
                                    "finish_reason": None
                                }]
                            )
                            current_tool_index += 1

                    elif etype == "response.function_call_arguments.delta":
                        item_id = event.get("item_id")
                        tinfo = active_tool_map.get(item_id)
                        tindex = tinfo[0] if tinfo else 0
                        delta = event.get("delta", "")
                        if delta:
                            yield ModelResponse(
                                id=resp_id,
                                object="chat.completion.chunk",
                                created=created,
                                model=model,
                                choices=[{
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [{
                                            "index": tindex,
                                            "function": {
                                                "arguments": delta
                                            }
                                        }]
                                    },
                                    "finish_reason": None
                                }]
                            )

                    elif etype == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if delta:
                            yield ModelResponse(
                                id=resp_id,
                                object="chat.completion.chunk",
                                created=created,
                                model=model,
                                choices=[{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
                            )
                except Exception as parse_err:
                    pass
        await resp.aclose()

    finish_reason = "tool_calls" if has_tool_calls else "stop"
    yield ModelResponse(
        id=resp_id,
        object="chat.completion.chunk",
        created=created,
        model=model,
        choices=[{"index": 0, "delta": {}, "finish_reason": finish_reason}]
    )

# --- 3.2. Google Antigravity Bridge ---
def _is_gemini_model(model_str):
    m = str(model_str).lower()
    return "gemini" in m or "antigravity" in m

def _map_antigravity_model(model_str):
    raw = model_str.split("/")[-1].lower()
    mapping = {
        "gemini-3.7-flash-thinking": "gemini-3.7-flash-low",
        "gemini-3.7-flash-tiered": "gemini-3.7-flash-low",
        "gemini-3.7-flash": "gemini-3.7-flash-low",
        "gemini-3.6-flash": "gemini-3.6-flash-low",
        "gemini-3.5-flash": "gemini-3.5-flash-extra-low",
        "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
        "gemini-3.1-pro": "gemini-3.1-pro-low",
        "gemini-3-flash": "gemini-3-flash",
        "gemini-3-pro": "gemini-3-pro-low",
        "gemini-2.5-pro": "gemini-2.5-pro",
        "gemini-2.5-flash-lite": "gemini-2.5-flash-lite",
        "gemini-2.5-flash": "gemini-2.5-flash",
    }
    for k, v in mapping.items():
        if k in raw:
            return v
    return "gemini-2.5-flash"

def _messages_to_antigravity_payload(model, messages, project_id):
    mapped_model = _map_antigravity_model(model)
    contents = []
    system_parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") in ["text", "input_text"]:
                        text_parts.append(part.get("text", ""))
            text = "".join(text_parts)
        else:
            text = str(content)

        if role == "system":
            system_parts.append({"text": text})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
        else:
            contents.append({"role": "user", "parts": [{"text": text}]})

    request_obj = {"contents": contents}
    if system_parts:
        request_obj["systemInstruction"] = {"parts": system_parts}

    return {
        "project": project_id,
        "requestId": str(uuid.uuid4()),
        "model": mapped_model,
        "userAgent": "antigravity",
        "requestType": "agent",
        "request": request_obj
    }

def _call_antigravity_sync(model, messages, token, project_id):
    url = "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
    payload = _messages_to_antigravity_payload(model, messages, project_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "antigravity/hub/2.8.0 (aidev_client; os_type=darwin; arch=arm64; cl=963137146)"
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    
    full_text = []
    prompt_tokens = 10
    completion_tokens = 10
    try:
        resp_handle = urllib.request.urlopen(req, timeout=45)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            fresh_token = _token_manager.get_google_token(force_refresh=True)
            if fresh_token:
                headers["Authorization"] = f"Bearer {fresh_token}"
                req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
                resp_handle = urllib.request.urlopen(req, timeout=45)
            else:
                raise
        elif e.code in [503, 404] and payload.get("model") != "gemini-3.7-flash-low":
            payload["model"] = "gemini-3.7-flash-low"
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            resp_handle = urllib.request.urlopen(req, timeout=45)
        else:
            raise

    with resp_handle as resp:
        for line in resp:
            line_str = line.decode("utf-8", "replace").strip()
            if line_str.startswith("data: "):
                data_str = line_str[6:]
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                    resp_obj = event.get("response", {})
                    candidates = resp_obj.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for p in parts:
                            txt = p.get("text", "")
                            if txt:
                                full_text.append(txt)
                    usage = resp_obj.get("usageMetadata", {})
                    if usage:
                        prompt_tokens = usage.get("promptTokenCount", prompt_tokens)
                        completion_tokens = usage.get("candidatesTokenCount", completion_tokens)
                except:
                    pass
    return "".join(full_text), prompt_tokens, completion_tokens

async def _stream_antigravity_generator(model, messages, token, project_id):
    from litellm import ModelResponse
    url = "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
    payload = _messages_to_antigravity_payload(model, messages, project_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "antigravity/hub/2.8.0 (aidev_client; os_type=darwin; arch=arm64; cl=963137146)"
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    
    resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()
    
    def run_request():
        try:
            req_obj = req
            try:
                r_handle = urllib.request.urlopen(req_obj, timeout=45)
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    fresh_token = _token_manager.get_google_token(force_refresh=True)
                    if fresh_token:
                        headers["Authorization"] = f"Bearer {fresh_token}"
                        req_obj = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
                        r_handle = urllib.request.urlopen(req_obj, timeout=45)
                    else:
                        raise
                elif e.code in [503, 404] and payload.get("model") != "gemini-3.7-flash-low":
                    payload["model"] = "gemini-3.7-flash-low"
                    req_obj = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
                    r_handle = urllib.request.urlopen(req_obj, timeout=45)
                else:
                    raise

            with r_handle as r:
                for line in r:
                    line_str = line.decode("utf-8", "replace").strip()
                    if line_str.startswith("data: "):
                        data_str = line_str[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            event = json.loads(data_str)
                            resp_obj = event.get("response", {})
                            candidates = resp_obj.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                for p in parts:
                                    txt = p.get("text", "")
                                    if txt:
                                        loop.call_soon_threadsafe(queue.put_nowait, txt)
                        except:
                            pass
        except Exception as e:
            print(f"[sitecustomize] Antigravity stream erro: {e}", file=sys.stderr)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=run_request, daemon=True).start()

    while True:
        delta = await queue.get()
        if delta is None:
            break
        yield ModelResponse(
            id=resp_id,
            object="chat.completion.chunk",
            created=created,
            model=model,
            choices=[{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
        )
    
    yield ModelResponse(
        id=resp_id,
        object="chat.completion.chunk",
        created=created,
        model=model,
        choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}]
    )

# --- 4. Monkey-patch litellm.acompletion e litellm.completion ---
try:
    import litellm

    _orig_acompletion = litellm.acompletion
    @functools.wraps(_orig_acompletion)
    async def _wrapped_acompletion(*args, **kwargs):
        model = str(kwargs.get("model", "") or (args[0] if len(args) > 0 else ""))
        messages = kwargs.get("messages") or (args[1] if len(args) > 1 else [])
        
        # Google Antigravity (Gemini) Bridge
        if _is_gemini_model(model):
            google_token = _token_manager.get_google_token()
            google_project = _token_manager.get_google_project_id()
            if google_token:
                if kwargs.get("stream", False):
                    return _stream_antigravity_generator(model, messages, google_token, google_project)
                else:
                    loop = asyncio.get_event_loop()
                    content, pt, ct = await loop.run_in_executor(None, _call_antigravity_sync, model, messages, google_token, google_project)
                    from litellm import ModelResponse
                    return ModelResponse(
                        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                        object="chat.completion",
                        created=int(time.time()),
                        model=model,
                        choices=[{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                        usage={"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
                    )

        # OpenAI Codex Bridge
        codex_token = _token_manager.get_codex_token()
        if codex_token and _is_codex_model(model):
            tools = kwargs.get("tools")
            if kwargs.get("stream", False):
                return _stream_codex_generator(model, messages, codex_token, tools=tools)
            else:
                loop = asyncio.get_event_loop()
                content, tool_calls = await loop.run_in_executor(None, _call_codex_sync, model, messages, codex_token, tools)
                from litellm import ModelResponse
                msg = {"role": "assistant"}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                    finish_reason = "tool_calls"
                else:
                    msg["content"] = content
                    finish_reason = "stop"
                return ModelResponse(
                    id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    object="chat.completion",
                    created=int(time.time()),
                    model=model,
                    choices=[{"index": 0, "message": msg, "finish_reason": finish_reason}],
                    usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
                )
                
        return await _orig_acompletion(*args, **_inject_claude_prompt(kwargs, args))

    litellm.acompletion = _wrapped_acompletion
    if hasattr(litellm, "main"):
        litellm.main.acompletion = _wrapped_acompletion

    _orig_completion = litellm.completion
    @functools.wraps(_orig_completion)
    def _wrapped_completion(*args, **kwargs):
        model = str(kwargs.get("model", "") or (args[0] if len(args) > 0 else ""))
        messages = kwargs.get("messages") or (args[1] if len(args) > 1 else [])

        # Google Antigravity (Gemini) Bridge
        if _is_gemini_model(model):
            google_token = _token_manager.get_google_token()
            google_project = _token_manager.get_google_project_id()
            if google_token:
                content, pt, ct = _call_antigravity_sync(model, messages, google_token, google_project)
                from litellm import ModelResponse
                return ModelResponse(
                    id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    object="chat.completion",
                    created=int(time.time()),
                    model=model,
                    choices=[{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                    usage={"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
                )

        # OpenAI Codex Bridge
        codex_token = _token_manager.get_codex_token()
        if codex_token and _is_codex_model(model):
            tools = kwargs.get("tools")
            content, tool_calls = _call_codex_sync(model, messages, codex_token, tools=tools)
            from litellm import ModelResponse
            msg = {"role": "assistant"}
            if tool_calls:
                msg["tool_calls"] = tool_calls
                finish_reason = "tool_calls"
            else:
                msg["content"] = content
                finish_reason = "stop"
            return ModelResponse(
                id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                object="chat.completion",
                created=int(time.time()),
                model=model,
                choices=[{"index": 0, "message": msg, "finish_reason": finish_reason}],
                usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
            )
        return _orig_completion(*args, **_inject_claude_prompt(kwargs, args))

    litellm.completion = _wrapped_completion
    if hasattr(litellm, "main"):
        litellm.main.completion = _wrapped_completion

    print("[sitecustomize] LiteLLM Claude Code + OpenAI Codex + Google Antigravity with Auto-Refresh enabled", file=sys.stderr)
except Exception as _e:
    print(f"[sitecustomize] Error loading bridges in LiteLLM: {_e}", file=sys.stderr)
