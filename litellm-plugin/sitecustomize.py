import functools
import sys
import os
import json
import time
import datetime
import uuid
import asyncio
import base64
import hashlib
import collections
import contextvars
import ssl
import urllib.request
import urllib.parse
import threading
import httpx

import litellm
import litellm.main
import litellm.router
from litellm.types.utils import (
    ModelResponse,
    ModelResponseStream,
    StreamingChoices,
    Delta,
    Usage,
    PromptTokensDetailsWrapper,
    CompletionTokensDetailsWrapper,
)

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
    print(f"[sitecustomize] litellm mcp spec-server patch applied: {_patched}", file=sys.stderr)
except Exception as _e:
    print(f"[sitecustomize] litellm mcp patch skipped: {_e}", file=sys.stderr)

# --- 1b. MCP streamable-http patch: the 4096-byte peek breaks UTF-8 ---
# handle_streamable_http_mcp() reads only the first _MCP_ROUTING_PEEK_MAX_BYTES
# (4096) of the body to decide whether it is an 'initialize'. json.loads() on
# that prefix raises UnicodeDecodeError when the cut lands mid multi-byte
# character, and _is_initialize_request() only catches (JSONDecodeError,
# TypeError) -> HTTP 500 on any tool call > 4 KiB containing accents
# (reproduced 2026-08-30 with wiki_write_page: 7167 bytes fails, 7168 passes).
# A truncated body is never an initialize (initialize is small), so returning
# False is the correct behaviour.
_MCP_SERVER_MOD = "litellm.proxy._experimental.mcp_server.server"

def _patch_mcp_initialize_peek(_mod):
    try:
        _orig = _mod._is_initialize_request

        @functools.wraps(_orig)
        def _is_initialize_request_safe(body):
            if not body:
                return False
            try:
                data = json.loads(body)
            except Exception:
                return False
            return isinstance(data, dict) and data.get("method") == "initialize"

        _mod._is_initialize_request = _is_initialize_request_safe
        print("[sitecustomize] litellm mcp initialize-peek patch aplicado", file=sys.stderr)
    except Exception as _e:
        print(f"[sitecustomize] litellm mcp initialize-peek patch ignorado: {_e}", file=sys.stderr)

if _MCP_SERVER_MOD in sys.modules:
    _patch_mcp_initialize_peek(sys.modules[_MCP_SERVER_MOD])
else:
    # the module imports the whole proxy_server, so it is not imported here
    # interpreter startup), the patch is applied at the end of its exec_module.
    import importlib.abc
    import importlib.util

    class _McpServerPatchFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname != _MCP_SERVER_MOD:
                return None
            try:
                sys.meta_path.remove(self)
            except ValueError:
                pass
            spec = importlib.util.find_spec(fullname)
            if spec is None or spec.loader is None:
                return None
            _exec = spec.loader.exec_module

            def exec_module(module, _exec=_exec):
                _exec(module)
                _patch_mcp_initialize_peek(module)

            spec.loader.exec_module = exec_module
            return spec

    sys.meta_path.insert(0, _McpServerPatchFinder())

# --- OAuth token ownership ---
# The credential agent (quota-dashboard server or the desktop app) is the ONLY
# owner and refresher of the three refresh tokens. This process is strictly a
# consumer: it reads credentials and never exchanges or writes them.
#
# Why: Anthropic and OpenAI issue single-use rotating refresh tokens. Two
# independent refreshers (the agent's sweep plus a refresher inside this
# plugin) performed read-modify-write on the same token without a lock; whoever
# rotated first invalidated the other's copy, producing an `invalid_grant` loop
# that forced a manual re-login. Observed 2026-09-01 (12:47-12:57 WEST).
#
# If a token expires, refreshing it is the agent's job; here the credential
# source is simply re-read.

# --- 2. Token manager: re-reads the credential source in memory ---
# Secret-backed env vars are snapshots taken at container start. The source is
# re-read periodically so a credential saved by the agent becomes live without
# a restart.
_TOKEN_SYNC_INTERVAL_S = 10
_TOKEN_FIELDS = (
    "ANTHROPIC_OAUTH_TOKEN",
    "ANTHROPIC_REFRESH_TOKEN",
    "OPENAI_CODEX_OAUTH_TOKEN",
    "OPENAI_CODEX_REFRESH_TOKEN",
    "GOOGLE_ANTIGRAVITY_OAUTH_TOKEN",
    "GOOGLE_ANTIGRAVITY_REFRESH_TOKEN",
    "GOOGLE_ANTIGRAVITY_PROJECT_ID",
)

# Kubernetes: the agent syncs credentials into this Secret.
_TOKEN_SECRET_NAMESPACE = os.environ.get("LITELLM_SECRET_NAMESPACE", "litellm")
_TOKEN_SECRET_NAME = os.environ.get("LITELLM_SECRET_NAME", "litellm-secrets")

# Docker / desktop: the agent writes credentials.json and shares the volume.
# docker-compose mounts it at /app/quota-data and passes no token env vars, so
# without this source the plugin would have no credentials at all.
_CREDENTIALS_FILES = tuple(
    path for path in (
        os.environ.get("TOKENGATEWAY_CREDENTIALS_PATH"),
        os.environ.get("CREDENTIALS_PATH"),
        "/app/quota-data/credentials.json",
        "/app/data/credentials.json",
    ) if path
)

# credentials.json provider key -> env field names used across this module.
_CREDENTIALS_FIELD_MAP = {
    "anthropic": {"access": "ANTHROPIC_OAUTH_TOKEN", "refresh": "ANTHROPIC_REFRESH_TOKEN"},
    "openai-codex": {"access": "OPENAI_CODEX_OAUTH_TOKEN", "refresh": "OPENAI_CODEX_REFRESH_TOKEN"},
    "google-antigravity": {
        "access": "GOOGLE_ANTIGRAVITY_OAUTH_TOKEN",
        "refresh": "GOOGLE_ANTIGRAVITY_REFRESH_TOKEN",
        "projectId": "GOOGLE_ANTIGRAVITY_PROJECT_ID",
    },
}


def _read_tokens_from_secret():
    """Read the credentials the agent synced into the Kubernetes Secret."""
    try:
        sa_token = open('/var/run/secrets/kubernetes.io/serviceaccount/token').read()
        ca = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
        host = os.environ.get('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')
        port = os.environ.get('KUBERNETES_SERVICE_PORT', '443')
        ctx = ssl.create_default_context(cafile=ca)
        req = urllib.request.Request(
            f'https://{host}:{port}/api/v1/namespaces/{_TOKEN_SECRET_NAMESPACE}'
            f'/secrets/{_TOKEN_SECRET_NAME}',
            headers={'Authorization': f'Bearer {sa_token}'},
        )
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            data = json.loads(response.read().decode()).get('data', {})
        return {
            name: base64.b64decode(data[name]).decode()
            for name in _TOKEN_FIELDS
            if data.get(name)
        }
    except FileNotFoundError:
        return None  # not running in Kubernetes
    except Exception as error:
        print(f"[TokenManager] Warning: failed to read {_TOKEN_SECRET_NAME}: {error}", file=sys.stderr)
        return None


def _read_tokens_from_credentials_file():
    """Read the credentials.json the agent writes on the shared volume."""
    for path in _CREDENTIALS_FILES:
        try:
            with open(path) as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            continue
        except Exception as error:
            print(f"[TokenManager] Warning: failed to read {path}: {error}", file=sys.stderr)
            continue
        if not isinstance(payload, dict):
            continue
        values = {}
        for provider, fields in _CREDENTIALS_FIELD_MAP.items():
            credential = payload.get(provider)
            if not isinstance(credential, dict):
                continue
            for key, field in fields.items():
                value = credential.get(key)
                if value:
                    values[field] = str(value)
        if values:
            return values
    return None


def _read_tokens():
    """Kubernetes Secret first, then the agent's credentials.json."""
    return _read_tokens_from_secret() or _read_tokens_from_credentials_file()


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
        self._last_sync = 0.0

    def _sync_tokens_locked(self, force=False):
        now = time.time()
        if not force and now - self._last_sync < _TOKEN_SYNC_INTERVAL_S:
            return False
        self._last_sync = now
        values = _read_tokens()
        if not values:
            return False
        changed = False
        for attr, field in (
            ("_anthropic_token", "ANTHROPIC_OAUTH_TOKEN"),
            ("_anthropic_refresh", "ANTHROPIC_REFRESH_TOKEN"),
            ("_codex_token", "OPENAI_CODEX_OAUTH_TOKEN"),
            ("_codex_refresh", "OPENAI_CODEX_REFRESH_TOKEN"),
            ("_google_token", "GOOGLE_ANTIGRAVITY_OAUTH_TOKEN"),
            ("_google_refresh", "GOOGLE_ANTIGRAVITY_REFRESH_TOKEN"),
            ("_google_project_id", "GOOGLE_ANTIGRAVITY_PROJECT_ID"),
        ):
            value = values.get(field)
            if value and value != getattr(self, attr):
                setattr(self, attr, value)
                os.environ[field] = value
                changed = True
        if changed:
            print("[TokenManager] credentials refreshed from the agent", file=sys.stderr)
        return changed

    def get_anthropic_token(self, force_refresh=False):
        # `force_refresh` means "the token I hold was rejected": re-read the
        # source to pick up a rotation the agent already performed. It never
        # refreshes locally.
        with self._lock:
            self._sync_tokens_locked(force=force_refresh)
            return self._anthropic_token

    def get_codex_token(self, force_refresh=False):
        # Consumer only - see the ownership note at the top of this file.
        with self._lock:
            self._sync_tokens_locked(force=force_refresh)
            return self._codex_token

    def get_google_token(self, force_refresh=False):
        # Consumer only - see the ownership note at the top of this file.
        with self._lock:
            self._sync_tokens_locked(force=force_refresh)
            return self._google_token

    def get_google_project_id(self):
        with self._lock:
            self._sync_tokens_locked()
            return self._google_project_id

_token_manager = TokenManager()
# Thought signatures per tool call. An unbounded dict grows forever inside the
# proxy process: the signature only matters on the next turn, so a short FIFO
# window is kept.
_thought_signatures = collections.OrderedDict()
_THOUGHT_SIGNATURE_LIMIT = 512

def _remember_thought_signature(call_id, signature):
    _thought_signatures[call_id] = signature
    _thought_signatures.move_to_end(call_id)
    while len(_thought_signatures) > _THOUGHT_SIGNATURE_LIMIT:
        _thought_signatures.popitem(last=False)

# --- 3. Wire protocol adapters for Claude Code, OpenAI Codex, and Google Antigravity ---
# Anthropic OAuth validates this exact Agent SDK identity as the sole system message.
CLAUDE_CODE_PROMPT = "You are a Claude agent, built on Anthropic's Claude Agent SDK."

# Effort -> thinking budget. The 8192 ceiling comes from the short TPM
# window of the Max subscription (omp goes up to 32768 on regular keys).
_ANTHROPIC_EFFORT_BUDGET = {
    "minimal": 1024, "low": 2048, "medium": 4096,
    "high": 8192, "xhigh": 8192, "max": 8192,
}
# Claude 4.7+/5.x only reason in adaptive mode, and the display default is
# "omitted": they return a signed thinking block whose text is empty.
# display="summarized" is what returns the reasoning text (omp #1373).
def _normalize_effort(value):
    """The /v1/responses route sends `reasoning: {effort, summary}` and the
    litellm translator forwards the whole object as reasoning_effort.
    Treating it as a string put "{'effort': 'medium', ...}" on the wire,
    which the Codex backend rejects with 400 Invalid value.

            Returns (effort, summary)."""
    if isinstance(value, dict):
        effort = value.get("effort")
        summary = value.get("summary")
    else:
        effort = value
        summary = None
    effort = str(effort or "").strip().lower() or None
    summary = str(summary).strip().lower() if summary else None
    return effort, summary

_ANTHROPIC_ADAPTIVE_EFFORT = {
    "minimal": "low", "low": "low", "medium": "medium",
    "high": "high", "xhigh": "high", "max": "high",
}
_ANTHROPIC_ADAPTIVE_MODELS = (
    "opus-4-7", "opus-4-8", "opus-5", "sonnet-5", "haiku-5", "fable-5", "mythos-5",
)

def _is_anthropic_adaptive(model):
    lowered = str(model).lower()
    return any(marker in lowered for marker in _ANTHROPIC_ADAPTIVE_MODELS)

# Ported from @oh-my-pi/pi-ai (Azr/Mzr/X$s). Anthropic caches everything *up to*
# a breakpoint, so OMP places no marker on `system` at all: two markers on the
# last messages already cover tools + system + the whole history. Two adjacent
# anchors (not one) keep a valid entry to extend from as the conversation grows.
ANTHROPIC_CACHE_BREAKPOINT_MESSAGES = 2

# X$s: retention defaults to "short", i.e. a bare ephemeral marker (5m). The 1h
# TTL is opt-in ("long" retention on models that support it) because a 1h write
# costs 2x base against 1.25x for 5m.
def _anthropic_cache_control():
    return {"type": "ephemeral"}

# Azr: blocks that carry reasoning are never valid anchors.
_ANTHROPIC_UNCACHEABLE_BLOCKS = ("thinking", "redacted_thinking", "fallback")

# Hosted tools: LiteLLM emits `server_tool_use` with no cache_control
# (factory.py:1971), so such a call cannot serve as an anchor.
_ANTHROPIC_SERVER_TOOL_PREFIX = "srvtoolu_"

def _anthropic_markable_message(message):
    """Whether a breakpoint can be attached to this message.

    OMP marks the Anthropic wire, where a tool result is a `tool_result` block
    inside a `user` turn, so its rolling window always lands on the last two
    turns. We see the OpenAI shape: a tool result is its own `role: "tool"`
    message and an assistant tool call carries `content: None`. LiteLLM 1.90.2
    forwards a breakpoint for both, but reads it from a different level
    (`litellm_core_utils/prompt_templates/factory.py`):
      - `role: "tool"`  -> message level, `convert_to_anthropic_tool_result:1844`
      - `tool_calls[i]` -> inside the call, `convert_to_anthropic_tool_invoke:2003`
      - text blocks     -> on the block itself
    Refusing the first two pinned the window to the head of the conversation:
    on a turn ending in a tool result, 67% of the prompt was re-read at full
    price (measured: opus-5 pt=8697, read=2876).
    """
    if not isinstance(message, dict):
        return False
    role = message.get("role")
    if role == "tool" or message.get("tool_call_id"):
        return True
    if role not in ("user", "assistant", "developer"):
        return False
    if _anthropic_tool_call_anchor(message) is not None:
        return True
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(
            isinstance(block, dict)
            and block.get("type") not in _ANTHROPIC_UNCACHEABLE_BLOCKS
            and str(block.get("text", "")).strip()
            for block in content
        )
    return False

def _anthropic_tool_call_anchor(message):
    """Index of the last tool call LiteLLM agrees to mark.

    `convert_to_anthropic_tool_invoke:1952` skips anything not `type: "function"`.
    """
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return None
    for index in range(len(calls) - 1, -1, -1):
        call = calls[index]
        if not isinstance(call, dict):
            continue
        if call.get("type") != "function":
            continue
        if str(call.get("id") or "").startswith(_ANTHROPIC_SERVER_TOOL_PREFIX):
            continue
        return index
    return None

def _count_cache_breakpoints(messages):
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("cache_control"):
            total += 1
        for call in message.get("tool_calls") or ():
            if isinstance(call, dict) and call.get("cache_control"):
                total += 1
        content = message.get("content")
        if isinstance(content, list):
            total += sum(
                1 for block in content
                if isinstance(block, dict) and block.get("cache_control")
            )
    return total

def _mark_cache_breakpoint(message):
    """Azr: mark the last non-reasoning anchor; bail when one is already marked."""
    control = _anthropic_cache_control()
    if message.get("role") == "tool" or message.get("tool_call_id"):
        if message.get("cache_control") is not None:
            return False
        message["cache_control"] = control
        return True
    # An assistant turn can carry both text and tool calls; on the Anthropic
    # wire the `tool_use` follows the text, so it is the anchor covering more.
    call_index = _anthropic_tool_call_anchor(message)
    if call_index is not None:
        call = message["tool_calls"][call_index]
        if call.get("cache_control") is not None:
            return False
        call["cache_control"] = control
        return True
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = [{"type": "text", "text": content, "cache_control": control}]
        return True
    if not isinstance(content, list):
        return False
    for index in range(len(content) - 1, -1, -1):
        block = content[index]
        if not isinstance(block, dict):
            continue
        if block.get("type") in _ANTHROPIC_UNCACHEABLE_BLOCKS:
            continue
        if block.get("cache_control") is not None:
            return False
        if not str(block.get("text", "")).strip():
            continue
        block["cache_control"] = control
        return True
    return False

def _apply_conversation_cache(messages):
    """Mzr: anchor breakpoints on the last markable messages."""
    anchors = [i for i, m in enumerate(messages) if _anthropic_markable_message(m)]
    if not anchors:
        return 0
    # A trailing synthetic "Continue." nudge is not a useful anchor.
    last = messages[anchors[-1]]
    if (
        last.get("role") == "user"
        and last.get("content") == "Continue."
        and len(anchors) > 1
    ):
        anchors = anchors[:-1]
    marked = 0
    for index in reversed(anchors[-ANTHROPIC_CACHE_BREAKPOINT_MESSAGES:]):
        message = dict(messages[index])
        if isinstance(message.get("content"), list):
            message["content"] = [
                dict(block) if isinstance(block, dict) else block
                for block in message["content"]
            ]
        if isinstance(message.get("tool_calls"), list):
            message["tool_calls"] = [
                dict(call) if isinstance(call, dict) else call
                for call in message["tool_calls"]
            ]
        if _mark_cache_breakpoint(message):
            messages[index] = message
            marked += 1
    return marked

def _inject_claude_prompt(kwargs, args=None):
    model = str(kwargs.get("model", "") or (args[0] if args and len(args) > 0 else "")).lower()
    if "claude" not in model and "anthropic" not in model:
        return kwargs

    fresh_anthropic_token = _token_manager.get_anthropic_token()
    if fresh_anthropic_token:
        kwargs["api_key"] = fresh_anthropic_token
        os.environ["ANTHROPIC_OAUTH_TOKEN"] = fresh_anthropic_token

    extra_headers = kwargs.setdefault("extra_headers", {})
    if isinstance(extra_headers, dict):
        # omp's beta list (buildCoworkBetas). Two notes:
        #  - redact-thinking-2026-02-12 makes Anthropic return signed
        #    thinking blocks with no text, killing reasoning across the
        #    whole Claude family (measured on sonnet-4-6: 74 chars without
        #    the beta, 0 chars with it).
        #  - context-1m-2025-08-07 is left out: it returns credit 429s on
        #    subscription tokens.
        extra_headers["anthropic-beta"] = (
            "claude-code-20250219,interleaved-thinking-2025-05-14,"
            "thinking-token-count-2026-05-13,context-management-2025-06-27,"
            "prompt-caching-scope-2026-01-05,mid-conversation-system-2026-04-07,"
            "advanced-tool-use-2025-11-20,effort-2025-11-24,fallback-credit-2026-06-01"
        )
        extra_headers["User-Agent"] = "claude-cli/2.1.246 (external, claude-desktop)"
        extra_headers["anthropic-dangerous-direct-browser-access"] = "true"
        extra_headers["x-app"] = "cli"

    reasoning, _summary = _normalize_effort(kwargs.get("reasoning_effort"))
    thinking = kwargs.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        kwargs.pop("thinking", None)
        thinking = None
    if reasoning == "none":
        kwargs.pop("reasoning_effort", None)
        kwargs.pop("thinking", None)
        reasoning = None
        thinking = None
    thinking_active = bool(thinking or reasoning in _ANTHROPIC_EFFORT_BUDGET)
    temperature = kwargs.get("temperature")
    if temperature is not None and float(temperature) != 1.0:
        if thinking_active:
            kwargs["temperature"] = 1.0
        else:
            kwargs.pop("reasoning_effort", None)
            kwargs.pop("thinking", None)
            thinking_active = False

    # Claude Max reserves max_tokens against its short TPM window. Bound extended
    # thinking requests so OMP's 64k-128k defaults do not cause an immediate 429.
    # OMP sends max_completion_tokens (OpenAI-style), not max_tokens; both must be
    # clamped in lockstep or the uncapped key still reaches Anthropic downstream.
    if thinking_active:
        if isinstance(thinking, dict):
            budget = min(thinking.get("budget_tokens") or 4096, 8192)
            if thinking.get("type") != "adaptive":
                thinking["budget_tokens"] = budget
        else:
            budget = _ANTHROPIC_EFFORT_BUDGET.get(reasoning, 4096)
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
        kwargs.pop("reasoning_effort", None)
        if _is_anthropic_adaptive(model):
            # budget_tokens is rejected/ignored by these models; adaptive
            # plus output_config.effort is the only supported shape.
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["output_config"] = {
                "effort": _ANTHROPIC_ADAPTIVE_EFFORT.get(reasoning, "medium")
            }

        for _tok_key in ("max_tokens", "max_completion_tokens"):
            _tok_val = kwargs.get(_tok_key)
            if _tok_val is None or _tok_val > 16384:
                kwargs[_tok_key] = 16384
            elif _tok_val <= budget:
                kwargs[_tok_key] = budget + 2048
        if "max_completion_tokens" in kwargs:
            kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")

    messages = kwargs.get("messages") or (args[1] if args and len(args) > 1 else [])
    if not isinstance(messages, list):
        return kwargs

    # Keep OAuth's required Agent SDK identity isolated in system. Anthropic returns
    # 429 when any client instruction shares this message or appears in another system
    # message. Move client system instructions to the first user turn and cache them.
    system_parts = []
    non_system_messages = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            non_system_messages.append(message)
            continue

        content = message.get("content", "")
        if isinstance(content, str):
            system_parts.append(content)
        elif isinstance(content, list):
            system_parts.extend(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )

    client_system_prompt = "\n\n".join(
        part.strip().replace(CLAUDE_CODE_PROMPT, "").strip()
        for part in system_parts
        if part.strip()
    )
    claude_identity = {
        "role": "system",
        "content": [{"type": "text", "text": CLAUDE_CODE_PROMPT}],
    }

    instructions_index = None
    if client_system_prompt:
        cached_instructions = {
            "type": "text",
            "text": (
                "<client_system_instructions>\n"
                f"{client_system_prompt}\n"
                "</client_system_instructions>"
            ),
        }
        first_user_index = next(
            (
                index
                for index, message in enumerate(non_system_messages)
                if isinstance(message, dict) and message.get("role") == "user"
            ),
            None,
        )
        if first_user_index is None:
            non_system_messages.insert(0, {"role": "user", "content": [cached_instructions]})
            instructions_index = 0
        else:
            first_user = dict(non_system_messages[first_user_index])
            user_content = first_user.get("content", "")
            if isinstance(user_content, list):
                first_user["content"] = [cached_instructions] + list(user_content)
            else:
                first_user["content"] = [
                    cached_instructions,
                    {"type": "text", "text": str(user_content)},
                ]
            non_system_messages[first_user_index] = first_user
            instructions_index = first_user_index

    if not _apply_conversation_cache(non_system_messages) and instructions_index is not None:
        # Safety net: this is only left for a conversation with no markable
        # message at all. Now that `role: "tool"` and `tool_calls` are anchors,
        # a real conversation always takes the normal path above.
        fallback = dict(non_system_messages[instructions_index])
        fallback["content"] = [
            dict(block) if isinstance(block, dict) else block
            for block in fallback["content"]
        ]
        if _mark_cache_breakpoint(fallback):
            non_system_messages[instructions_index] = fallback

    kwargs["messages"] = [claude_identity] + non_system_messages
    return kwargs

# --- 3.1. OpenAI Codex Bridge ---
def _is_codex_model(model_str):
    m = str(model_str).lower()
    return "gpt-" in m or "codex" in m or m.startswith("gpt")

# Per-process transport state. The Codex backend uses these ids for session
# and prompt-cache affinity; omp sends them on every request.
_CODEX_INSTALLATION_ID = str(uuid.uuid4())
_CODEX_WINDOW_ID = str(uuid.uuid4())

def _codex_token_claims(tok):
    try:
        parts = str(tok).split(".")
        if len(parts) != 3:
            return {}
        padding = len(parts[1]) % 4
        padded = parts[1] + ("=" * (4 - padding) if padding else "")
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}

def _extract_account_id(tok):
    auth = _codex_token_claims(tok).get("https://api.openai.com/auth") or {}
    return auth.get("chatgpt_account_id")

def _build_codex_headers(tok, turn_id=None):
    claims = _codex_token_claims(tok)
    auth = claims.get("https://api.openai.com/auth") or {}
    session_id = str(claims.get("session_id") or _CODEX_WINDOW_ID)
    headers = {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
        "accept": "text/event-stream",
        "originator": "pi",
        "OpenAI-Beta": "responses=experimental",
        "User-Agent": "pi (linux; x86_64)",
        "session_id": session_id,
        "session-id": session_id,
        "x-codex-installation-id": _CODEX_INSTALLATION_ID,
        "x-codex-window-id": _CODEX_WINDOW_ID,
        "x-codex-turn-metadata": json.dumps({
            "turn_id": turn_id or str(uuid.uuid4()),
            "installation_id": _CODEX_INSTALLATION_ID,
            "request_kind": "chat",
        }),
    }
    acc = auth.get("chatgpt_account_id")
    if acc:
        headers["chatgpt-account-id"] = acc
    # The backend returns x-codex-turn-state and expects it back on the
    # next turn: it is the session's transport state.
    if _CODEX_TURN_STATE[0]:
        headers["x-codex-turn-state"] = _CODEX_TURN_STATE[0]
    # Enterprise workspaces pinned to a region answer 401 "Workspace is
    # not authorized in this region" for requests from elsewhere. The header
    # only travels when the token carries the claim; personal accounts do not.
    residency = auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency")
    if residency and str(residency) != "no_constraint":
        headers["x-openai-internal-codex-residency"] = str(residency)
    return headers

def _codex_prompt_cache_key(messages):
    """Stable key per conversation: the backend only reuses the cached
    prefix when the request repeats the same key, and the stable prefix is
    the head of the conversation (developer/system + first user turn)."""
    digest = hashlib.sha256()
    seen = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "user")
        if role not in ("system", "developer", "user"):
            continue
        digest.update(role.encode("utf-8"))
        digest.update(_content_to_text(message.get("content")).encode("utf-8"))
        seen += 1
        if seen >= 2:
            break
    return digest.hexdigest()[:32] if seen else None
def _content_to_text(content):
    if isinstance(content, list):
        return "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text")
        )
    return str(content) if content is not None else ""

# Codex rejects `detail: "original"`; omp rewrites it to "auto".
_CODEX_IMAGE_DETAILS = ("auto", "low", "high")

def _codex_image_part(part):
    """chat-completions image_url -> Responses input_image."""
    image = part.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    if not url:
        return None
    detail = (image.get("detail") if isinstance(image, dict) else None) or part.get("detail") or "auto"
    if str(detail).lower() not in _CODEX_IMAGE_DETAILS:
        detail = "auto"
    return {"type": "input_image", "image_url": str(url), "detail": str(detail).lower()}

def _codex_file_part(part):
    """chat-completions file -> Responses input_file."""
    spec = part.get("file") if isinstance(part.get("file"), dict) else part
    data = spec.get("file_data") or spec.get("data")
    file_id = spec.get("file_id")
    if not data and not file_id:
        return None
    item = {"type": "input_file"}
    if spec.get("filename"):
        item["filename"] = str(spec["filename"])
    if file_id:
        item["file_id"] = str(file_id)
    else:
        item["file_data"] = str(data)
    return item

def _content_to_codex_parts(content, assistant=False):
    """Keeps images and files instead of dropping them. Before this, any
    multimodal request reached the model as text only."""
    text_type = "output_text" if assistant else "input_text"
    if not isinstance(content, list):
        text = str(content) if content is not None else ""
        return [{"type": text_type, "text": text}] if text else []
    parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text"):
            if part.get("text"):
                parts.append({"type": text_type, "text": str(part["text"])})
        elif kind in ("image_url", "input_image"):
            built = _codex_image_part(part)
            if built:
                parts.append(built)
        elif kind in ("file", "input_file"):
            built = _codex_file_part(part)
            if built:
                parts.append(built)
    return parts

# Responses identifies each tool call by (call_id, item_id). omp joins both
# into "<call_id>|<item_id>" so the replay rebuilds the exact pair; without
# it, parallel calls drift out of alignment.
def _codex_composite_call_id(call_id, item_id):
    if call_id and item_id and call_id != item_id:
        return f"{call_id}|{item_id}"
    return call_id or item_id or f"call_{uuid.uuid4().hex[:8]}"

def _codex_split_call_id(value):
    return str(value or "").split("|", 1)[0]

def _repair_codex_tool_pairs(items):
    calls = {
        item.get("call_id")
        for item in items
        if item.get("type") == "function_call" and item.get("call_id")
    }
    outputs = {
        item.get("call_id")
        for item in items
        if item.get("type") == "function_call_output" and item.get("call_id")
    }
    repaired = []
    for item in items:
        call_id = item.get("call_id")
        if item.get("type") == "function_call_output" and call_id not in calls:
            repaired.append({
                "type": "message",
                "role": "assistant",
                "content": f"[Previous tool result; call_id={call_id}]: {_content_to_text(item.get('output'))}",
            })
        else:
            repaired.append(item)
        if item.get("type") == "function_call" and call_id not in outputs:
            repaired.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": "[No tool output recorded: the tool call was interrupted before it produced a result.]",
            })
    return repaired

def _messages_to_codex_input(messages):
    codex_input = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content")
        if role == "tool":
            codex_input.append({
                "type": "function_call_output",
                "call_id": _codex_split_call_id(message.get("tool_call_id")),
                "output": _content_to_text(content),
            })
            continue

        codex_role = "developer" if role == "system" else role
        if codex_role not in ("user", "assistant", "developer"):
            codex_role = "user"
        parts = _content_to_codex_parts(content, assistant=codex_role == "assistant")
        if parts:
            codex_input.append({
                "type": "message",
                "role": codex_role,
                "content": parts,
            })

        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            arguments = function.get("arguments") or "{}"
            codex_input.append({
                "type": "function_call",
                "call_id": _codex_split_call_id(tool_call.get("id")),
                "name": function.get("name") or "",
                "arguments": json.dumps(arguments) if isinstance(arguments, dict) else arguments,
            })
    return _repair_codex_tool_pairs(codex_input)

# Backend-hosted tools (web search, image generation, shell, ...) carry no
# `function`: they travel with their own spec and were dropped here.
_CODEX_HOSTED_TOOL_TYPES = (
    "web_search", "web_search_preview", "image_generation", "code_interpreter",
    "local_shell", "computer", "computer_use_preview", "custom", "mcp", "file_search",
)

def _tools_to_codex_tools(tools):
    if not tools:
        return None
    codex_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") in _CODEX_HOSTED_TOOL_TYPES:
            codex_tools.append(dict(tool))
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        if not function.get("name"):
            continue
        codex_tools.append({
            "type": "function",
            "name": function["name"],
            "description": function.get("description") or "",
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        })
    return codex_tools or None

def _codex_tool_choice(choice):
    if not isinstance(choice, dict):
        return choice
    function = choice.get("function")
    if choice.get("type") == "function" and isinstance(function, dict) and function.get("name"):
        return {"type": "function", "name": function["name"]}
    return choice

# --- Upstream usage normalisation (ported from @oh-my-pi/pi-ai) ---
# Both bridges answer requests themselves, so whatever they omit here is lost:
# LiteLLM then falls back to token_counter estimates and every cache hit is
# invisible in /spend/logs.
def _bridge_usage(prompt_tokens, completion_tokens, cached_tokens=0, reasoning_tokens=0, total_tokens=None):
    prompt_tokens = max(0, int(prompt_tokens or 0))
    completion_tokens = max(0, int(completion_tokens or 0))
    cached_tokens = max(0, int(cached_tokens or 0))
    reasoning_tokens = max(0, int(reasoning_tokens or 0))
    usage = Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=int(total_tokens) if total_tokens else prompt_tokens + completion_tokens,
    )
    if cached_tokens:
        usage.prompt_tokens_details = PromptTokensDetailsWrapper(cached_tokens=cached_tokens)
        # LiteLLM's spend logging reads this attribute, not the details wrapper.
        setattr(usage, "cache_read_input_tokens", cached_tokens)
    if reasoning_tokens:
        usage.completion_tokens_details = CompletionTokensDetailsWrapper(
            reasoning_tokens=reasoning_tokens
        )
    return usage

def _google_usage(meta):
    """omp-google-shared.ts: promptTokenCount *includes* cached tokens, so it is
    subtracted to avoid double-counting, and thoughts count as output."""
    cached = meta.get("cachedContentTokenCount") or 0
    thinking = meta.get("thoughtsTokenCount") or 0
    return _bridge_usage(
        prompt_tokens=(meta.get("promptTokenCount") or 0) - cached,
        completion_tokens=(meta.get("candidatesTokenCount") or 0) + thinking,
        cached_tokens=cached,
        reasoning_tokens=thinking,
        total_tokens=meta.get("totalTokenCount"),
    )

def _codex_usage(meta):
    """OMP `eRe`: unlike Google, input_tokens is not reduced by cached_tokens."""
    details = meta.get("input_tokens_details") or {}
    out_details = meta.get("output_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:
        cached = meta.get("prompt_cache_hit_tokens") or 0
    return _bridge_usage(
        prompt_tokens=meta.get("input_tokens") or 0,
        completion_tokens=meta.get("output_tokens") or 0,
        cached_tokens=cached,
        reasoning_tokens=out_details.get("reasoning_tokens") or 0,
        total_tokens=meta.get("total_tokens"),
    )

def _usage_chunk(model, response_id, created, usage):
    """Final stream chunk carrying the real usage; without it LiteLLM estimates.

    `choices` must carry an empty entry instead of being `[]`: the
    LiteLLMCompletionStreamingIterator on the /v1/responses route calls
    _is_reasoning_end(chunk), which does chunk.choices[0].delta unguarded.
    With an empty list that raises IndexError and the stream dies before
    emitting response.completed, leaving the client waiting for the
    terminal event forever.
    """
    chunk = ModelResponseStream(
        id=response_id,
        created=created,
        model=model,
        choices=[StreamingChoices(index=0, delta=Delta(), finish_reason=None)],
    )
    setattr(chunk, "usage", usage)
    return chunk

# --- Spend logging for bridge-served responses ---
# The Codex and Antigravity bridges answer requests themselves, so LiteLLM never
# wraps them in CustomStreamWrapper and no success callback fires. Without this,
# successful bridge completions are absent from /spend/logs entirely and only
# their exceptions get recorded by the proxy error handler.
async def _emit_bridge_success(logging_obj, response, start_time, end_time):
    if logging_obj is None or response is None:
        return
    try:
        await logging_obj.async_success_handler(response, start_time, end_time)
    except Exception as log_err:
        print(f"[sitecustomize] bridge spend log falhou: {log_err}", file=sys.stderr)

async def _logged_bridge_stream(generator, logging_obj, messages, start_time):
    """Pass bridge chunks through untouched, then emit one spend-log row.

    Reassembly is best effort: a logging failure must never break an otherwise
    healthy stream. Client disconnects skip the record rather than awaiting
    inside a closing generator.
    """
    chunks = []
    async for chunk in generator:
        chunks.append(chunk)
        yield chunk
    if logging_obj is None or not chunks:
        return
    try:
        end_time = datetime.datetime.now()
        response = litellm.stream_chunk_builder(
            chunks, messages=messages, start_time=start_time, end_time=end_time
        )
        await _emit_bridge_success(logging_obj, response, start_time, end_time)
    except Exception as log_err:
        print(f"[sitecustomize] bridge stream spend log failed: {log_err}", file=sys.stderr)


# The /v1/responses route runs in the same task, so a contextvar is enough to
# flag the path without touching kwargs (litellm consumes
# use_chat_completions_api before it reaches acompletion).
_RESPONSES_ROUTE = contextvars.ContextVar("omp_bridge_responses_route", default=False)

class _BridgeStreamWrapper(litellm.CustomStreamWrapper):
    """The litellm Responses translator only accepts ModelResponse or
    CustomStreamWrapper: with the bridges' async generator it returned
    500 "Unexpected response type: <class 'async_generator'>" on
    /v1/responses with stream=true. This wrapper satisfies the isinstance check
    and hands over the already-built chunks without reprocessing them.
    LiteLLMCompletionStreamingIterator only uses `logging_obj` and `__anext__`.
    """

    def __init__(self, generator, model, logging_obj=None, custom_llm_provider=None):
        self.completion_stream = generator
        self.model = model
        self.logging_obj = logging_obj
        self.custom_llm_provider = custom_llm_provider
        self.sent_first_chunk = True
        self.sent_last_chunk = False
        self.received_finish_reason = None
        self.special_tokens = []
        self.holding_chunk = ""
        self.complete_response = ""
        self.chunks = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.completion_stream.__anext__()

    def __iter__(self):
        return self

    def __next__(self):
        raise StopIteration

def _bridge_stream_result(generator, model, logging_obj):
    if _RESPONSES_ROUTE.get():
        return _BridgeStreamWrapper(generator, model, logging_obj=logging_obj)
    return generator
def _strip_codex_output_limits(kwargs):
    for key in ("max_tokens", "max_output_tokens", "max_completion_tokens"):
        kwargs.pop(key, None)
    if isinstance(kwargs.get("extra_body"), dict):
        for key in ("max_tokens", "max_output_tokens", "max_completion_tokens"):
            kwargs["extra_body"].pop(key, None)

# A ChatGPT account (Plus/Pro) rejects the 5.4 family with
# "The 'gpt-5.4' model is not supported when using Codex with a ChatGPT
# account" - 5.5 is the equivalent the backend serves. This map is the single
# source of truth for the wire id, so it also covers config aliases that
# still point at 5.4.
_CODEX_WIRE_ALIASES = {
    "gpt-6": "gpt-6-astra",
    "gpt6": "gpt-6-astra",
    "gpt-5": "gpt-5.5",
    "gpt5": "gpt-5.5",
    "codex": "gpt-5.5",
    "gpt-5.4": "gpt-5.5",
    "gpt-5.4-mini": "gpt-5.5",
}

# With reasoning off, GPT-5.6+ Responses still reserve "juice"; omp pins it
# with a trailing developer item
# (getJuiceValue: none 0, minimal 2, low 4, medium 8, high 48, xhigh 112,
# max 960).
_CODEX_JUICE = {
    "none": 0, "minimal": 2, "low": 4, "medium": 8,
    "high": 48, "xhigh": 112, "max": 960,
}

def _codex_wire_generation(req_model):
    pieces = str(req_model).lower().split("-")
    try:
        return float(pieces[1]) if len(pieces) > 1 else 0.0
    except ValueError:
        return 0.0

def _codex_request_body(model, messages, tools, extra_kwargs):
    req_model = model.split("/")[-1]
    req_model = _CODEX_WIRE_ALIASES.get(req_model.lower(), req_model)
    req_model = _CODEX_UNSUPPORTED.get(req_model.lower(), req_model)
    body = {
        "model": req_model,
        "store": False,
        "stream": True,
        "input": _messages_to_codex_input(messages),
    }
    cache_key = _codex_prompt_cache_key(messages)
    if cache_key:
        body["prompt_cache_key"] = cache_key
    codex_tools = _tools_to_codex_tools(tools)
    if codex_tools:
        body["tools"] = codex_tools
    _extra = extra_kwargs or {}
    tool_choice = _codex_tool_choice(_extra.get("tool_choice"))
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    # The Codex backend only returns reasoning text when the request carries the
    # `reasoning` object (measured: without it, zero
    # response.reasoning_summary_text.delta events). omp always sends an effort,
    # hence the "medium" default here instead of omitting it.
    effort, summary = _normalize_effort(_extra.get("reasoning_effort"))
    effort = effort or "medium"
    summary = summary if summary in ("auto", "detailed", "concise") else "auto"
    if effort == "none":
        if _codex_wire_generation(req_model) >= 5.6:
            body["input"] = list(body["input"]) + [{
                "type": "message",
                "role": "developer",
                "content": [{
                    "type": "input_text",
                    "text": f"# Juice: {_CODEX_JUICE['none']} !important",
                }],
            }]
    else:
        body["reasoning"] = {
            "effort": effort,
            "summary": summary,
            "context": "all_turns",
        }
    if _extra.get("service_tier") is not None:
        body["service_tier"] = _extra["service_tier"]
    return body

# Quota state read from responses. Without it, exhaustion is only discovered
# when the first 429 arrives.
_CODEX_QUOTA = {}
_CODEX_TURN_STATE = [None]
# The backend rejects models the account does not serve; instead of blindly
# propagating the 400, the rejection is learned and routed to the served one.
_CODEX_UNSUPPORTED = {}
_CODEX_FALLBACK_MODEL = "gpt-5.5"

def _codex_capture_response_state(resp):
    headers = getattr(resp, "headers", None) or {}
    snapshot = {}
    for key, value in headers.items():
        lowered = str(key).lower()
        if lowered.startswith("x-codex-"):
            if lowered == "x-codex-turn-state":
                _CODEX_TURN_STATE[0] = value
                continue
            snapshot[lowered] = value
    if snapshot:
        _CODEX_QUOTA.clear()
        _CODEX_QUOTA.update(snapshot)
    return snapshot

def _codex_quota_headers():
    """Reexpostos ao cliente como llm_provider-* pelo proxy."""
    return dict(_CODEX_QUOTA)

def _codex_unsupported_model(err_text):
    return "is not supported when using Codex" in str(err_text)

def _codex_remember_unsupported(body):
    """Only remaps names that are deliberate aliases of a served model.

    Substituting an arbitrary name (gpt-4.1, o3-mini, gpt-3.5-turbo) with
    gpt-5.5 returned 200 with the `model` field echoing the requested name: the
    client believed it talked to a different model, and billing and
    comparisons started lying. For those, the upstream 400 is the correct
    answer.
    """
    wire = str(body.get("model") or "").lower()
    if not wire or wire == _CODEX_FALLBACK_MODEL:
        return False
    if wire not in _CODEX_WIRE_ALIASES and wire not in _CODEX_UNSUPPORTED:
        print(
            f"[sitecustomize] codex: {wire} is not served by this account and is not a known alias; propagating the error",
            file=sys.stderr,
        )
        return False
    _CODEX_UNSUPPORTED[wire] = _CODEX_FALLBACK_MODEL
    body["model"] = _CODEX_FALLBACK_MODEL
    print(
        f"[sitecustomize] codex: alias {wire} rejected by the account, routing to {_CODEX_FALLBACK_MODEL}",
        file=sys.stderr,
    )
    return True

def _codex_reset_credits(token):
    """Lists reset credits. Redeeming spends a limited resource, so it only runs
    when CODEX_AUTO_REDEEM_RESET_CREDITS is set."""
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0, read=30.0, write=10.0)) as client:
            resp = client.send(client.build_request(
                "GET",
                "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits",
                headers=_build_codex_headers(token),
            ))
            if resp.status_code != 200:
                return []
            payload = json.loads(resp.read().decode("utf-8", "replace"))
            return payload.get("credits") or []
    except Exception:
        return []

def _codex_redeem_reset_credit(token):
    if not os.environ.get("CODEX_AUTO_REDEEM_RESET_CREDITS"):
        return False
    credits = _codex_reset_credits(token)
    if not credits:
        return False
    soonest = min(credits, key=lambda c: str(c.get("expires_at") or c.get("expiresAt") or "~"))
    credit_id = soonest.get("id") or soonest.get("credit_id")
    if not credit_id:
        return False
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0, read=30.0, write=10.0)) as client:
            resp = client.send(client.build_request(
                "POST",
                "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume",
                json={"credit_id": credit_id, "redeem_request_id": str(uuid.uuid4())},
                headers=_build_codex_headers(token),
            ))
            ok = resp.status_code == 200
            print(f"[sitecustomize] codex: reset credit {credit_id} redeemed={ok}", file=sys.stderr)
            return ok
    except Exception:
        return False

# omp's watchdogs: 300s for the first event and 300s of inactivity between
# events. The httpx read timeout is exactly the second one.
_CODEX_TIMEOUT = httpx.Timeout(None, connect=30.0, read=300.0, write=60.0)

def _codex_open(client, url, body, headers_box, token):
    for _attempt in range(3):
        resp = client.send(client.build_request("POST", url, json=body, headers=headers_box[0]), stream=True)
        _codex_capture_response_state(resp)
        if resp.status_code == 200:
            return resp
        if resp.status_code == 401:
            resp.close()
            fresh_token = _token_manager.get_codex_token(force_refresh=True)
            if not fresh_token:
                raise Exception("OpenAI Codex error 401: token could not be refreshed")
            headers_box[0] = _build_codex_headers(fresh_token)
            continue
        status = resp.status_code
        err_text = resp.read().decode("utf-8", "replace")
        resp.close()
        if status == 400 and _codex_unsupported_model(err_text) and _codex_remember_unsupported(body):
            continue
        if status == 429 and _codex_redeem_reset_credit(token):
            continue
        raise Exception(f"OpenAI Codex error {status}: {err_text}")
    raise Exception("OpenAI Codex error: tentativas de abertura esgotadas")

async def _codex_open_async(client, url, body, headers_box, token):
    for _attempt in range(3):
        resp = await client.send(client.build_request("POST", url, json=body, headers=headers_box[0]), stream=True)
        _codex_capture_response_state(resp)
        if resp.status_code == 200:
            return resp
        if resp.status_code == 401:
            await resp.aclose()
            fresh_token = _token_manager.get_codex_token(force_refresh=True)
            if not fresh_token:
                raise Exception("OpenAI Codex error 401: token could not be refreshed")
            headers_box[0] = _build_codex_headers(fresh_token)
            continue
        status = resp.status_code
        err_text = (await resp.aread()).decode("utf-8", "replace")
        await resp.aclose()
        if status == 400 and _codex_unsupported_model(err_text) and _codex_remember_unsupported(body):
            continue
        if status == 429 and _codex_redeem_reset_credit(token):
            continue
        raise Exception(f"OpenAI Codex error {status}: {err_text}")
    raise Exception("OpenAI Codex error: tentativas de abertura esgotadas")

def _call_codex_sync(model, messages, token, tools=None, extra_kwargs=None):
    url = "https://chatgpt.com/backend-api/codex/responses"
    body = _codex_request_body(model, messages, tools, extra_kwargs)
    headers = _build_codex_headers(token)

    full_text = []
    reasoning_text = []
    tool_calls = []
    active_tools = {}
    usage_meta = {}
    terminal_seen = False

    with httpx.Client(timeout=_CODEX_TIMEOUT) as client:
        resp = _codex_open(client, url, body, [headers], token)

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
                            call_id = _codex_composite_call_id(item.get("call_id"), item.get("id"))
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
                    elif etype in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta"):
                        reasoning_text.append(event.get("delta", ""))
                    elif etype == "response.output_text.delta":
                        full_text.append(event.get("delta", ""))
                    elif etype in ("response.completed", "response.incomplete"):
                        terminal_seen = True
                        usage_meta = event.get("response", {}).get("usage") or {}
                    elif etype in ["response.failed", "error"]:
                        err_obj = event.get("response", {}).get("error") or event.get("message") or "Unknown error"
                        raise Exception(f"OpenAI Codex stream failed: {err_obj}")
                except Exception as parse_err:
                    if "OpenAI Codex stream failed" in str(parse_err):
                        raise parse_err
                    pass
        resp.close()

    # Only `response.completed`/`response.incomplete` close the response. A
    # stream cut before either is a transport failure: returning it as success
    # hands truncated output over as if it were complete.
    if not terminal_seen:
        raise Exception("OpenAI Codex stream ended without response.completed/response.incomplete")

    return "".join(full_text), "".join(reasoning_text), tool_calls, _codex_usage(usage_meta)

async def _stream_codex_generator(model, messages, token, tools=None, extra_kwargs=None):
    url = "https://chatgpt.com/backend-api/codex/responses"
    body = _codex_request_body(model, messages, tools, extra_kwargs)
    headers = _build_codex_headers(token)

    resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    active_tool_map = {}
    current_tool_index = 0
    has_tool_calls = False
    completion_status = "completed"
    usage_meta = {}
    terminal_seen = False
    whitespace_events = 0
    whitespace_bytes = 0

    async with httpx.AsyncClient(timeout=_CODEX_TIMEOUT) as client:
        resp = await _codex_open_async(client, url, body, [headers], token)

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
                            call_id = _codex_composite_call_id(item.get("call_id"), item.get("id"))
                            fn_name = item.get("name", "")
                            active_tool_map[item.get("id")] = (current_tool_index, call_id, fn_name)
                            yield ModelResponseStream(
                                id=resp_id,
                                created=created,
                                model=model,
                                choices=[StreamingChoices(
                                    index=0,
                                    delta=Delta(
                                        role="assistant",
                                        tool_calls=[{
                                            "index": current_tool_index,
                                            "id": call_id,
                                            "type": "function",
                                            "function": {
                                                "name": fn_name,
                                                "arguments": ""
                                            }
                                        }]
                                    ),
                                    finish_reason=None
                                )]
                            )
                            current_tool_index += 1

                    elif etype == "response.function_call_arguments.delta":
                        item_id = event.get("item_id")
                        tinfo = active_tool_map.get(item_id)
                        tindex = tinfo[0] if tinfo else 0
                        delta = event.get("delta", "")
                        # The backend sometimes loops emitting only whitespace in the arguments;
                        # without a breaker the stream never closes
                        # (omp's limits: 256 events / 16KB).
                        if delta and not delta.strip():
                            whitespace_events += 1
                            whitespace_bytes += len(delta)
                            if whitespace_events > 256 or whitespace_bytes > 16384:
                                raise Exception(
                                    "OpenAI Codex stream failed: whitespace tool-call argument loop"
                                )
                        if delta:
                            yield ModelResponseStream(
                                id=resp_id,
                                created=created,
                                model=model,
                                choices=[StreamingChoices(
                                    index=0,
                                    delta=Delta(
                                        tool_calls=[{
                                            "index": tindex,
                                            "function": {
                                                "arguments": delta
                                            }
                                        }]
                                    ),
                                    finish_reason=None
                                )]
                            )

                    elif etype in ["response.reasoning_text.delta", "response.reasoning_summary_text.delta"]:
                        delta = event.get("delta", "")
                        if delta:
                            yield ModelResponseStream(
                                id=resp_id,
                                created=created,
                                model=model,
                                choices=[StreamingChoices(
                                    index=0,
                                    delta=Delta(reasoning_content=delta),
                                    finish_reason=None
                                )]
                            )

                    elif etype in ["response.output_text.delta", "response.refusal.delta"]:
                        delta = event.get("delta", "")
                        if delta:
                            yield ModelResponseStream(
                                id=resp_id,
                                created=created,
                                model=model,
                                choices=[StreamingChoices(
                                    index=0,
                                    delta=Delta(content=delta),
                                    finish_reason=None
                                )]
                            )
                    elif etype in ("response.completed", "response.incomplete"):
                        resp_data = event.get("response", {})
                        terminal_seen = True
                        completion_status = resp_data.get("status", "completed")
                        usage_meta = resp_data.get("usage") or {}
                    elif etype in ["response.failed", "error"]:
                        err_obj = event.get("response", {}).get("error") or event.get("message") or "Unknown error"
                        raise Exception(f"OpenAI Codex stream failed: {err_obj}")
                except Exception as parse_err:
                    if "OpenAI Codex stream failed" in str(parse_err):
                        raise parse_err
                    pass
        await resp.aclose()
    if not terminal_seen:
        raise Exception("OpenAI Codex stream ended without response.completed/response.incomplete")
    finish_reason = "tool_calls" if has_tool_calls else ("length" if completion_status == "incomplete" else "stop")
    yield ModelResponseStream(
        id=resp_id,
        created=created,
        model=model,
        choices=[StreamingChoices(index=0, delta=Delta(), finish_reason=finish_reason)]
    )
    # Without a usage-bearing chunk LiteLLM falls back to token_counter estimates
    # and every cached token stays invisible in /spend/logs.
    yield _usage_chunk(model, resp_id, created, _codex_usage(usage_meta))
# --- 3.2. Google Antigravity Bridge ---
def _is_gemini_model(model_str):
    m = str(model_str).lower()
    return "gemini" in m or "antigravity" in m

# Endpoint failover remembering the last good host, like omp's
# AntigravityProviderSessionState. Both accept the same envelope.
_ANTIGRAVITY_HOSTS = (
    "https://daily-cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
)
_ANTIGRAVITY_PATH = "/v1internal:streamGenerateContent?alt=sse"
_antigravity_host_index = [0]

# Per-process agent/trajectory identity: omp's requestId has the form
# agent/<agentId>/<ts>/<trajectoryId>/<step>. `labels` and `sessionId` do NOT
# belong in the envelope -- this endpoint answers
# 400 Invalid JSON payload received. Unknown name "labels".
_ANTIGRAVITY_AGENT_ID = uuid.uuid4().hex[:8]
_ANTIGRAVITY_TRAJECTORY_ID = uuid.uuid4().hex[:8]
_antigravity_step = [0]
_GOOGLE_MAX_EMPTY_RETRIES = 3
_GOOGLE_EMPTY_RETRY_BASE_S = 1.0

def _antigravity_urls():
    start = _antigravity_host_index[0]
    count = len(_ANTIGRAVITY_HOSTS)
    return [
        _ANTIGRAVITY_HOSTS[(start + offset) % count] + _ANTIGRAVITY_PATH
        for offset in range(count)
    ]

def _antigravity_mark_host(url):
    for index, host in enumerate(_ANTIGRAVITY_HOSTS):
        if url.startswith(host):
            _antigravity_host_index[0] = index
            return

def _antigravity_request_id():
    _antigravity_step[0] += 1
    return "agent/{}/{}/{}/{}".format(
        _ANTIGRAVITY_AGENT_ID,
        int(time.time() * 1000),
        _ANTIGRAVITY_TRAJECTORY_ID,
        _antigravity_step[0],
    )

def _google_raise_in_band(event):
    """CCA returns errors inside the stream with HTTP 200. Swallowing them makes
    the request look like a successful empty response."""
    error = event.get("error") if isinstance(event, dict) else None
    if isinstance(error, dict) and int(error.get("code") or 0) >= 400:
        raise Exception(
            f"Google Antigravity error {error.get('code')}: {error.get('message') or error}"
        )
    feedback = (event.get("response") or {}).get("promptFeedback") if isinstance(event, dict) else None
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise Exception(f"Google Antigravity content blocked: {feedback.get('blockReason')}")

# Effort -> Gemini 3 thinkingLevel (the 2.x dialect uses thinkingBudget).
# MINIMAL never goes on the wire: gemini-3.8-* and gemini-3.1-pro answer
# 400 "Thinking level MINIMAL is not supported for this model" (only
# gemini-3.5-flash-* and 2.5-* accept it). omp's rule is to map to the lowest
# supported effort instead of inventing an unsupported value.
_GOOGLE_LOWEST_THINKING_LEVEL = "LOW"
_GOOGLE_THINKING_LEVEL = {
    "minimal": _GOOGLE_LOWEST_THINKING_LEVEL,
    "low": "LOW", "medium": "MEDIUM",
    "high": "HIGH", "xhigh": "HIGH", "max": "HIGH",
}

def _bridge_message(content, reasoning, tool_calls):
    """Assistant message in the OpenAI shape, with reasoning in the field LiteLLM
    standardises (`reasoning_content`) instead of losing it or mixing it into the
    visible content."""
    msg = {"role": "assistant"}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    else:
        msg["content"] = content
    if reasoning:
        msg["reasoning_content"] = reasoning
    return msg

_ANTIGRAVITY_USER_AGENT = "antigravity/hub/2.8.0 (aidev_client; os_type=darwin; arch=arm64; cl=963137146)"



def _attach_codex_quota(response):
    """The proxy re-exposes additional_headers as llm_provider-*: that is how the
    client sees quota without waiting for the first 429."""
    snapshot = _codex_quota_headers()
    if not snapshot:
        return response
    hidden = getattr(response, "_hidden_params", None)
    if not isinstance(hidden, dict):
        hidden = {}
    headers = dict(hidden.get("additional_headers") or {})
    headers.update(snapshot)
    hidden["additional_headers"] = headers
    try:
        response._hidden_params = hidden
    except Exception:
        pass
    return response

# CCA timeout: 300s of inactivity between events, like omp.
_GOOGLE_TIMEOUT = httpx.Timeout(None, connect=30.0, read=300.0, write=60.0)

# Flash models sometimes leak their internal planning JSON object into the
# visible text. Only a whole part that parses as an object carrying planning
# markers is discarded, so a legitimate JSON answer from the user is never
# swallowed.
_GOOGLE_LEAK_MARKERS = ("thought", "_i", "call", "paths", "command")

def _google_is_planning_leak(text):
    stripped = str(text).strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return False
    try:
        parsed = json.loads(stripped)
    except Exception:
        return False
    if not isinstance(parsed, dict):
        return False
    if "thought" in parsed and any(marker in parsed for marker in ("_i", "call", "paths", "command")):
        return True
    return "thought" in parsed and len(parsed) == 1
# The account's real catalogue via :fetchAvailableModels (body {"project": ...};
# `metadata`/`cloudaicompanionProject` return 400 on this endpoint).
_ANTIGRAVITY_MODEL_CACHE = {"at": 0.0, "ids": (), "info": {}}
_ANTIGRAVITY_MODEL_TTL_S = 600.0

# The catalogue advertises variants streamGenerateContent rejects:
# gemini-3.1-pro-high returns 400 INVALID_ARGUMENT (omp documents the same
# and routes to gemini-pro-agent).
_ANTIGRAVITY_BROKEN_WIRE = ("gemini-3.1-pro-high", "gemini-3-pro-high")

# Effort -> variant suffix, in preference order.
_ANTIGRAVITY_EFFORT_SUFFIXES = {
    "none": ("-extra-low", "-low", "", "-tiered"),
    "minimal": ("-extra-low", "-low", "", "-tiered"),
    "low": ("-low", "-extra-low", "", "-tiered"),
    "medium": ("-medium", "-low", "", "-tiered"),
    "high": ("-high", "-medium", "-low", ""),
    "xhigh": ("-high", "-medium", "-low", ""),
    "max": ("-high", "-medium", "-low", ""),
}
# Families with no usable suffix variant at the top of the scale.
_ANTIGRAVITY_EFFORT_OVERRIDES = {
    ("gemini-3.1-pro", "high"): "gemini-pro-agent",
    ("gemini-3.1-pro", "xhigh"): "gemini-pro-agent",
    ("gemini-3.1-pro", "max"): "gemini-pro-agent",
    ("gemini-3-pro", "high"): "gemini-pro-agent",
    ("gemini-3.5-flash", "high"): "gemini-3-flash-agent",
    ("gemini-3.5-flash", "xhigh"): "gemini-3-flash-agent",
    ("gemini-3.5-flash", "max"): "gemini-3-flash-agent",
}
_ANTIGRAVITY_SUFFIXES = ("-thinking", "-tiered", "-extra-low", "-low", "-medium", "-high", "-agent")

def _antigravity_available_models(token, project_id):
    now = time.time()
    cached = _ANTIGRAVITY_MODEL_CACHE
    if cached["ids"] and now - cached["at"] < _ANTIGRAVITY_MODEL_TTL_S:
        return cached["ids"]
    try:
        host = _ANTIGRAVITY_HOSTS[_antigravity_host_index[0]]
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0, read=30.0, write=10.0)) as client:
            resp = client.send(client.build_request(
                "POST", host + "/v1internal:fetchAvailableModels",
                json={"project": project_id},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": _ANTIGRAVITY_USER_AGENT,
                },
            ))
            if resp.status_code == 200:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
                models = payload.get("models") or {}
                ids = tuple(models.keys())
                if ids:
                    cached["at"] = now
                    cached["ids"] = ids
                    cached["info"] = models
    except Exception:
        pass
    return cached["ids"]

def _antigravity_base_family(model_str):
    base = str(model_str).split("/")[-1].lower()
    for suffix in _ANTIGRAVITY_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base

def _map_antigravity_model(model_str, effort=None):
    raw = model_str.split("/")[-1].lower()
    available = _ANTIGRAVITY_MODEL_CACHE["ids"]
    if available:
        base = _antigravity_base_family(raw)
        eff = str(effort or "medium").strip().lower() or "medium"
        candidates = []
        override = _ANTIGRAVITY_EFFORT_OVERRIDES.get((base, eff))
        if override:
            candidates.append(override)
        candidates.extend(base + suffix for suffix in _ANTIGRAVITY_EFFORT_SUFFIXES.get(eff, ("-low", "")))
        for candidate in candidates:
            if candidate in _ANTIGRAVITY_BROKEN_WIRE:
                continue
            if candidate in available:
                return candidate
    mapping = {
        "gemini-3.8-flash-thinking": "gemini-3.8-flash-low",
        "gemini-3.8-flash-tiered": "gemini-3.8-flash-low",
        "gemini-3.8-flash": "gemini-3.8-flash-low",
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
    # With no match, no other model is served silently: the gemini-* wildcard
    # would make any invented name answer as gemini-2.5-flash, with the `model`
    # field echoing the requested name.
    raise Exception(
        f"Google Antigravity: model '{raw}' is not served by this account "
        f"(no variant matches in the catalogue or the static map)"
    )

def _google_model_supports_function_ids(model):
    return model.split("/")[-1].lower().startswith("gemini-3")

def _google_text_parts(content):
    if isinstance(content, list):
        return [
            {"text": str(part.get("text", ""))}
            for part in content
            if isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text") and part.get("text")
        ]
    return [{"text": str(content)}] if content is not None and str(content) else []

def _google_tool_choice(choice, declarations):
    # Antigravity defaults to VALIDATED (omp does the same): the backend validates
    # the call against the schema before emitting it.
    if choice in (None, "auto"):
        return {"functionCallingConfig": {"mode": "VALIDATED"}}
    if choice == "none":
        return {"functionCallingConfig": {"mode": "NONE"}}
    if choice in ("required", "any"):
        return {"functionCallingConfig": {"mode": "ANY"}}
    if isinstance(choice, dict):
        function = choice.get("function") or choice
        name = function.get("name") if isinstance(function, dict) else None
        if name and any(declaration.get("name") == name for declaration in declarations):
            return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [name]}}
    return {"functionCallingConfig": {"mode": "VALIDATED"}}

def _tools_to_antigravity_tools(model, tools):
    if not tools:
        return None, []
    declarations = []
    use_legacy_parameters = model.split("/")[-1].lower().startswith("claude-")
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        schema = function.get("parameters") or {"type": "object", "properties": {}}
        declaration = {"name": name, "description": str(function.get("description") or "")}
        declaration["parameters" if use_legacy_parameters else "parametersJsonSchema"] = schema
        declarations.append(declaration)
    return ([{"functionDeclarations": declarations}] if declarations else None), declarations

def _tool_result_value(message):
    content = message.get("content")
    text = "".join(part.get("text", "") for part in content if isinstance(part, dict)) if isinstance(content, list) else str(content or "")
    value = {"error" if message.get("is_error") else "output": text}
    return value

def _messages_to_antigravity_payload(model, messages, project_id, tools=None, extra_kwargs=None):
    mapped_model = _map_antigravity_model(
        model, _normalize_effort((extra_kwargs or {}).get("reasoning_effort"))[0]
    )
    contents = []
    system_parts = []
    tool_names = {}
    supports_ids = _google_model_supports_function_ids(model)
    for message in messages:
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            call_id = (tool_call.get("id") or "").split("|", 1)[0]
            if call_id:
                tool_names[call_id] = function.get("name") or "tool"

    pending_tool_responses = []
    sentinel_used = False
    def flush_tool_responses():
        nonlocal pending_tool_responses
        if pending_tool_responses:
            contents.append({"role": "user", "parts": pending_tool_responses})
            pending_tool_responses = []

    for message in messages:
        role = message.get("role", "user") if isinstance(message, dict) else "user"
        if role != "tool":
            flush_tool_responses()
        content = message.get("content") if isinstance(message, dict) else None
        if role == "tool":
            encoded_call_id = message.get("tool_call_id") or ""
            call_id = encoded_call_id.split("|", 1)[0]
            function_response = {
                "name": message.get("name") or tool_names.get(call_id) or "tool",
                "response": _tool_result_value(message),
            }
            if supports_ids and call_id:
                function_response["id"] = call_id
            pending_tool_responses.append({"functionResponse": function_response})
            continue

        parts = _google_text_parts(content)
        if role == "system":
            system_parts.extend(parts)
        elif role == "assistant":
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function") or {}
                encoded_call_id = tool_call.get("id") or ""
                call_id, _, signature = encoded_call_id.partition("|")
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {"__raw": arguments}
                function_call = {"name": function.get("name") or "", "args": arguments}
                if supports_ids and call_id:
                    function_call["id"] = call_id
                part = {"functionCall": function_call}
                signature = tool_call.get("thoughtSignature") or tool_call.get("thought_signature") or signature or _thought_signatures.get(call_id)
                if signature:
                    part["thoughtSignature"] = signature
                elif not sentinel_used:
                    # CCA validates the first functionCall's signature in the turn; with no real
                    # signature, omp's sentinel is used, once per request.
                    #
                    part["thoughtSignature"] = "skip_thought_signature_validator"
                    sentinel_used = True
                parts.append(part)
            if parts:
                contents.append({"role": "model", "parts": parts})
        elif parts:
            contents.append({"role": "user", "parts": parts})
    flush_tool_responses()

    # OMP sends max_completion_tokens (OpenAI-style); accept both spellings or
    # the client's requested output ceiling is silently replaced by the default.
    _extra = extra_kwargs or {}
    max_tokens = _extra.get("max_tokens") or _extra.get("max_completion_tokens") or 64000
    request_obj = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
    # Omitting thinkingConfig makes CCA re-apply the server defaults and bill
    # thinking tokens without returning the text.
    # Antigravity uses *budget* transport (omp calls it budget mode); thinkingLevel
    # is the gemini-cli dialect. When the catalogue is available, the variant's
    # advertised thinkingBudget is used
    # (-low 1000, -medium 4000, -high -1 = dynamic, pro-agent 10001), and
    # minThinkingBudget to switch it off. Without the catalogue it falls back to
    # thinkingLevel, which is also accepted.
    _effort = _normalize_effort(_extra.get("reasoning_effort"))[0] or ""
    _info = _ANTIGRAVITY_MODEL_CACHE["info"].get(mapped_model) or {}
    _budget = _info.get("thinkingBudget")
    _min_budget = _info.get("minThinkingBudget")
    if _effort == "none":
        thinking_config = {"includeThoughts": False}
        if isinstance(_min_budget, int):
            thinking_config["thinkingBudget"] = _min_budget
        else:
            thinking_config["thinkingLevel"] = _GOOGLE_LOWEST_THINKING_LEVEL
    else:
        thinking_config = {"includeThoughts": True}
        if isinstance(_budget, int) and _budget > 0:
            thinking_config["thinkingBudget"] = _budget
        elif not isinstance(_budget, int):
            thinking_config["thinkingLevel"] = _GOOGLE_THINKING_LEVEL.get(_effort, "MEDIUM")
    request_obj["generationConfig"]["thinkingConfig"] = thinking_config
    # The native field is accepted with role "user" (same as omp) and with no
    # practical size limit -- measured with 2520 chars: HTTP 200. Splicing it into
    # the first user turn is no longer necessary.
    if system_parts:
        request_obj["systemInstruction"] = {"role": "user", "parts": system_parts}
    antigravity_tools, declarations = _tools_to_antigravity_tools(model, tools)
    if antigravity_tools:
        request_obj["tools"] = antigravity_tools
        request_obj["toolConfig"] = _google_tool_choice((extra_kwargs or {}).get("tool_choice"), declarations)
    return {
        "project": project_id,
        "requestId": _antigravity_request_id(),
        "model": mapped_model,
        "userAgent": "antigravity",
        "requestType": "agent",
        "request": request_obj,
    }

def _antigravity_open(client, payload, headers):
    """Tries the endpoints in order (last good first), refreshing the token on
    401 and downgrading the model on 404/503, like omp."""
    last = None
    for url in _antigravity_urls():
        resp = client.send(client.build_request("POST", url, json=payload, headers=headers), stream=True)
        if resp.status_code == 401:
            resp.close()
            fresh_token = _token_manager.get_google_token(force_refresh=True)
            if not fresh_token:
                raise Exception("Google Antigravity error 401: token could not be refreshed")
            headers["Authorization"] = f"Bearer {fresh_token}"
            resp = client.send(client.build_request("POST", url, json=payload, headers=headers), stream=True)
        if resp.status_code in (404, 503) and payload.get("model") != "gemini-3.7-flash-low":
            resp.close()
            payload["model"] = "gemini-3.7-flash-low"
            resp = client.send(client.build_request("POST", url, json=payload, headers=headers), stream=True)
        if resp.status_code == 200:
            _antigravity_mark_host(url)
            return resp
        last = (url, resp.status_code, resp.read().decode("utf-8", "replace"))
        resp.close()
    url, status, body = last
    raise Exception(f"Google Antigravity error {status} ({url}): {body}")

def _antigravity_collect(resp):
    full_text = []
    reasoning_text = []
    tool_calls = []
    usage_meta = {}
    for line in resp.iter_lines():
        line_str = line.strip()
        if not line_str.startswith("data: "):
            continue
        data_str = line_str[6:].strip()
        if data_str == "[DONE]":
            break
        try:
            event = json.loads(data_str)
        except Exception:
            continue
        _google_raise_in_band(event)
        resp_obj = event.get("response") or {}
        candidates = resp_obj.get("candidates") or []
        if candidates:
            for p in candidates[0].get("content", {}).get("parts", []) or []:
                txt = p.get("text", "")
                if txt:
                    if p.get("thought"):
                        reasoning_text.append(txt)
                    elif not _google_is_planning_leak(txt):
                        full_text.append(txt)
                fc = p.get("functionCall")
                if fc:
                    tsig = p.get("thoughtSignature")
                    call_id = fc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                    if tsig:
                        _remember_thought_signature(call_id, tsig)
                    tool_calls.append({
                        "id": call_id,
                        "type": "function",
                        "function": {"name": fc.get("name", ""), "arguments": json.dumps(fc.get("args") or {})},
                    })
        usage = resp_obj.get("usageMetadata") or {}
        if usage:
            usage_meta = usage
    return "".join(full_text), "".join(reasoning_text), tool_calls, usage_meta

def _call_antigravity_sync(model, messages, token, project_id, tools=None, extra_kwargs=None):
    _antigravity_available_models(token, project_id)
    payload = _messages_to_antigravity_payload(model, messages, project_id, tools=tools, extra_kwargs=extra_kwargs)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": _ANTIGRAVITY_USER_AGENT,
    }

    delay = _GOOGLE_EMPTY_RETRY_BASE_S
    content = reasoning = ""
    tool_calls = []
    usage_meta = {}
    with httpx.Client(timeout=_GOOGLE_TIMEOUT) as client:
        # Gemini occasionally closes with finishReason STOP and zero parts; omp asks
        # again instead of returning an empty turn.
        for attempt in range(_GOOGLE_MAX_EMPTY_RETRIES + 1):
            resp = _antigravity_open(client, payload, headers)
            try:
                content, reasoning, tool_calls, usage_meta = _antigravity_collect(resp)
            finally:
                resp.close()
            if content or reasoning or tool_calls or attempt >= _GOOGLE_MAX_EMPTY_RETRIES:
                break
            time.sleep(delay)
            delay *= 2
            payload["requestId"] = _antigravity_request_id()

    return content, reasoning, tool_calls, _google_usage(usage_meta)

async def _antigravity_open_async(client, payload, headers):
    last = None
    for url in _antigravity_urls():
        resp = await client.send(client.build_request("POST", url, json=payload, headers=headers), stream=True)
        if resp.status_code == 401:
            await resp.aclose()
            fresh_token = _token_manager.get_google_token(force_refresh=True)
            if not fresh_token:
                raise Exception("Google Antigravity error 401: token could not be refreshed")
            headers["Authorization"] = f"Bearer {fresh_token}"
            resp = await client.send(client.build_request("POST", url, json=payload, headers=headers), stream=True)
        if resp.status_code in (404, 503) and payload.get("model") != "gemini-3.7-flash-low":
            await resp.aclose()
            payload["model"] = "gemini-3.7-flash-low"
            resp = await client.send(client.build_request("POST", url, json=payload, headers=headers), stream=True)
        if resp.status_code == 200:
            _antigravity_mark_host(url)
            return resp
        last = (url, resp.status_code, (await resp.aread()).decode("utf-8", "replace"))
        await resp.aclose()
    url, status, body = last
    raise Exception(f"Google Antigravity error {status} ({url}): {body}")

async def _stream_antigravity_once(model, messages, token, project_id, tools=None, extra_kwargs=None, payload=None):
    payload = payload if payload is not None else _messages_to_antigravity_payload(
        model, messages, project_id, tools=tools, extra_kwargs=extra_kwargs
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": _ANTIGRAVITY_USER_AGENT,
    }

    resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    current_tool_index = 0
    has_tool_calls = False
    usage_meta = {}

    async with httpx.AsyncClient(timeout=_GOOGLE_TIMEOUT) as client:
        resp = await _antigravity_open_async(client, payload, headers)

        async for line in resp.aiter_lines():
            line_str = line.strip()
            if line_str.startswith("data: "):
                data_str = line_str[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except Exception:
                    continue
                # In-band errors arrive with HTTP 200; propagate instead of returning a
                # silently empty turn.
                _google_raise_in_band(event)
                try:
                    resp_obj = event.get("response", {})
                    usage_meta = resp_obj.get("usageMetadata") or usage_meta
                    candidates = resp_obj.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for p in parts:
                            txt = p.get("text", "")
                            if txt:
                                is_thought = bool(p.get("thought", False))
                                if not is_thought and _google_is_planning_leak(txt):
                                    continue
                                delta_obj = Delta(reasoning_content=txt) if is_thought else Delta(content=txt)
                                yield ModelResponseStream(
                                    id=resp_id,
                                    created=created,
                                    model=model,
                                    choices=[StreamingChoices(index=0, delta=delta_obj, finish_reason=None)]
                                )
                            fc = p.get("functionCall")
                            if fc:
                                has_tool_calls = True
                                tsig = p.get("thoughtSignature")
                                call_id = fc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                                if tsig:
                                    _remember_thought_signature(call_id, tsig)
                                fn_name = fc.get("name", "")
                                fn_args = json.dumps(fc.get("args") or {})

                                yield ModelResponseStream(
                                    id=resp_id,
                                    created=created,
                                    model=model,
                                    choices=[StreamingChoices(
                                        index=0,
                                        delta=Delta(
                                            role="assistant",
                                            tool_calls=[{
                                                "index": current_tool_index,
                                                "id": call_id,
                                                "type": "function",
                                                "function": {
                                                    "name": fn_name,
                                                    "arguments": ""
                                                }
                                            }]
                                        ),
                                        finish_reason=None
                                    )]
                                )
                                yield ModelResponseStream(
                                    id=resp_id,
                                    created=created,
                                    model=model,
                                    choices=[StreamingChoices(
                                        index=0,
                                        delta=Delta(
                                            tool_calls=[{
                                                "index": current_tool_index,
                                                "function": {
                                                    "arguments": fn_args
                                                }
                                            }]
                                        ),
                                        finish_reason=None
                                    )]
                                )
                                current_tool_index += 1
                except Exception:
                    pass
        await resp.aclose()

    finish_reason = "tool_calls" if has_tool_calls else "stop"
    yield ModelResponseStream(
        id=resp_id,
        created=created,
        model=model,
        choices=[StreamingChoices(index=0, delta=Delta(), finish_reason=finish_reason)]
    )
    # Without a usage-bearing chunk LiteLLM falls back to token_counter estimates
    # and every implicitly cached token stays invisible in /spend/logs.
    yield _usage_chunk(model, resp_id, created, _google_usage(usage_meta))

def _stream_chunk_is_meaningful(chunk):
    try:
        delta = chunk.choices[0].delta
    except Exception:
        return False
    return bool(
        getattr(delta, "content", None)
        or getattr(delta, "reasoning_content", None)
        or getattr(delta, "tool_calls", None)
    )

async def _stream_antigravity_generator(model, messages, token, project_id, tools=None, extra_kwargs=None):
    """Retries while the stream closes with no content. Leading non-meaningful
    chunks are held back until a meaningful one arrives, so the finish/usage of an
    empty attempt never reaches the client."""
    _antigravity_available_models(token, project_id)
    payload = _messages_to_antigravity_payload(model, messages, project_id, tools=tools, extra_kwargs=extra_kwargs)
    delay = _GOOGLE_EMPTY_RETRY_BASE_S
    for attempt in range(_GOOGLE_MAX_EMPTY_RETRIES + 1):
        pending = []
        meaningful = False
        async for chunk in _stream_antigravity_once(
            model, messages, token, project_id, tools=tools, extra_kwargs=extra_kwargs, payload=payload
        ):
            if meaningful:
                yield chunk
            elif _stream_chunk_is_meaningful(chunk):
                meaningful = True
                for held in pending:
                    yield held
                pending = []
                yield chunk
            else:
                pending.append(chunk)
        if meaningful or attempt >= _GOOGLE_MAX_EMPTY_RETRIES:
            for held in pending:
                yield held
            return
        await asyncio.sleep(delay)
        delay *= 2
        payload["requestId"] = _antigravity_request_id()

# --- 4. Monkey-patch litellm.main.acompletion e litellm.main.completion ---
try:
    _orig_acompletion = litellm.main.acompletion
    async def _wrapped_acompletion(*args, **kwargs):
        # Normalise positional args (model, messages) into kwargs
        if len(args) > 0 and "model" not in kwargs:
            kwargs["model"] = args[0]
        if len(args) > 1 and "messages" not in kwargs:
            kwargs["messages"] = args[1]
        args = ()
        model = str(kwargs.get("model", ""))
        messages = kwargs.get("messages") or []
        logging_obj = kwargs.get("litellm_logging_obj")
        start_time = datetime.datetime.now()

        # Google Antigravity (Gemini) Bridge
        if _is_gemini_model(model):
            google_token = _token_manager.get_google_token() or _token_manager.get_google_token(force_refresh=True)
            if not google_token:
                raise Exception("Google Antigravity OAuth token unavailable or expired")
            google_project = _token_manager.get_google_project_id()
            tools = kwargs.get("tools")
            if kwargs.get("stream", False):
                return _bridge_stream_result(_logged_bridge_stream(
                    _stream_antigravity_generator(model, messages, google_token, google_project, tools=tools, extra_kwargs=kwargs),
                    logging_obj, messages, start_time,
                ), model, logging_obj)
            else:
                loop = asyncio.get_event_loop()
                content, reasoning, tool_calls, usage = await loop.run_in_executor(None, _call_antigravity_sync, model, messages, google_token, google_project, tools, kwargs)
                msg = _bridge_message(content, reasoning, tool_calls)
                finish_reason = "tool_calls" if tool_calls else "stop"
                response = ModelResponse(
                    id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    object="chat.completion",
                    created=int(time.time()),
                    model=model,
                    choices=[{"index": 0, "message": msg, "finish_reason": finish_reason}],
                    usage=usage
                )
                await _emit_bridge_success(logging_obj, response, start_time, datetime.datetime.now())
                return response

        # OpenAI Codex Bridge
        if _is_codex_model(model):
            _strip_codex_output_limits(kwargs)
            codex_token = _token_manager.get_codex_token() or _token_manager.get_codex_token(force_refresh=True)
            if not codex_token:
                raise Exception("OpenAI Codex OAuth token unavailable or expired")
            tools = kwargs.get("tools")
            if kwargs.get("stream", False):
                return _bridge_stream_result(_logged_bridge_stream(
                    _stream_codex_generator(model, messages, codex_token, tools=tools, extra_kwargs=kwargs),
                    logging_obj, messages, start_time,
                ), model, logging_obj)
            else:
                loop = asyncio.get_event_loop()
                content, reasoning, tool_calls, usage = await loop.run_in_executor(None, _call_codex_sync, model, messages, codex_token, tools, kwargs)
                msg = _bridge_message(content, reasoning, tool_calls)
                finish_reason = "tool_calls" if tool_calls else "stop"
                response = ModelResponse(
                    id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    object="chat.completion",
                    created=int(time.time()),
                    model=model,
                    choices=[{"index": 0, "message": msg, "finish_reason": finish_reason}],
                    usage=usage
                )
                _attach_codex_quota(response)
                await _emit_bridge_success(logging_obj, response, start_time, datetime.datetime.now())
                return response

        return await _orig_acompletion(*args, **_inject_claude_prompt(kwargs, args))

    _orig_completion = litellm.main.completion
    def _wrapped_completion(*args, **kwargs):
        # Normalise positional args (model, messages) into kwargs
        if len(args) > 0 and "model" not in kwargs:
            kwargs["model"] = args[0]
        if len(args) > 1 and "messages" not in kwargs:
            kwargs["messages"] = args[1]
        args = ()
        model = str(kwargs.get("model", ""))
        messages = kwargs.get("messages") or []

        # Google Antigravity (Gemini) Bridge
        if _is_gemini_model(model):
            google_token = _token_manager.get_google_token()
            google_project = _token_manager.get_google_project_id()
            if google_token:
                tools = kwargs.get("tools")
                content, reasoning, tool_calls, usage = _call_antigravity_sync(model, messages, google_token, google_project, tools=tools, extra_kwargs=kwargs)
                msg = _bridge_message(content, reasoning, tool_calls)
                finish_reason = "tool_calls" if tool_calls else "stop"
                return ModelResponse(
                    id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    object="chat.completion",
                    created=int(time.time()),
                    model=model,
                    choices=[{"index": 0, "message": msg, "finish_reason": finish_reason}],
                    usage=usage
                )

        # OpenAI Codex Bridge
        if _is_codex_model(model):
            _strip_codex_output_limits(kwargs)
            codex_token = _token_manager.get_codex_token() or _token_manager.get_codex_token(force_refresh=True)
            if not codex_token:
                raise Exception("OpenAI Codex OAuth token unavailable or expired")
            tools = kwargs.get("tools")
            content, reasoning, tool_calls, usage = _call_codex_sync(model, messages, codex_token, tools=tools, extra_kwargs=kwargs)
            msg = _bridge_message(content, reasoning, tool_calls)
            finish_reason = "tool_calls" if tool_calls else "stop"
            return _attach_codex_quota(ModelResponse(
                id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                object="chat.completion",
                created=int(time.time()),
                model=model,
                choices=[{"index": 0, "message": msg, "finish_reason": finish_reason}],
                usage=usage
            ))
        return _orig_completion(*args, **_inject_claude_prompt(kwargs, args))
    # Proxy routes through Router.acompletion/completion, not necessarily the
    # module functions above. Intercept both paths before LiteLLM translates
    # OpenAI max_tokens into the unsupported Responses max_output_tokens.
    _orig_router_acompletion = litellm.Router.acompletion
    async def _wrapped_router_acompletion(self, model, messages, stream=False, **kwargs):
        logging_obj = kwargs.get("litellm_logging_obj")
        start_time = datetime.datetime.now()
        if _is_gemini_model(model):
            token = _token_manager.get_google_token() or _token_manager.get_google_token(force_refresh=True)
            if not token:
                raise Exception("Google Antigravity OAuth token unavailable or expired")
            project = _token_manager.get_google_project_id()
            if stream:
                return _bridge_stream_result(_logged_bridge_stream(
                    _stream_antigravity_generator(model, messages, token, project, tools=kwargs.get("tools"), extra_kwargs=kwargs),
                    logging_obj, messages, start_time,
                ), model, logging_obj)
            content, reasoning, tool_calls, usage = await asyncio.get_event_loop().run_in_executor(
                None, _call_antigravity_sync, model, messages, token, project, kwargs.get("tools"), kwargs
            )
            message = _bridge_message(content, reasoning, tool_calls)
            response = ModelResponse(id=f"chatcmpl-{uuid.uuid4().hex[:12]}", object="chat.completion", created=int(time.time()), model=model, choices=[{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}], usage=usage)
            await _emit_bridge_success(logging_obj, response, start_time, datetime.datetime.now())
            return response
        if _is_codex_model(model):
            _strip_codex_output_limits(kwargs)
            token = _token_manager.get_codex_token() or _token_manager.get_codex_token(force_refresh=True)
            if not token:
                raise Exception("OpenAI Codex OAuth token unavailable or expired")
            if stream:
                return _bridge_stream_result(_logged_bridge_stream(
                    _stream_codex_generator(model, messages, token, tools=kwargs.get("tools"), extra_kwargs=kwargs),
                    logging_obj, messages, start_time,
                ), model, logging_obj)
            content, reasoning, tool_calls, usage = await asyncio.get_event_loop().run_in_executor(
                None, _call_codex_sync, model, messages, token, kwargs.get("tools"), kwargs
            )
            message = _bridge_message(content, reasoning, tool_calls)
            response = _attach_codex_quota(ModelResponse(id=f"chatcmpl-{uuid.uuid4().hex[:12]}", object="chat.completion", created=int(time.time()), model=model, choices=[{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}], usage=usage))
            await _emit_bridge_success(logging_obj, response, start_time, datetime.datetime.now())
            return response
        return await _orig_router_acompletion(self, model=model, messages=messages, stream=stream, **kwargs)

    _orig_router_completion = litellm.Router.completion
    def _wrapped_router_completion(self, model, messages, **kwargs):
        if _is_gemini_model(model) or _is_codex_model(model):
            raise RuntimeError("LiteLLM proxy must route managed subscription models asynchronously")
        return _orig_router_completion(self, model=model, messages=messages, **kwargs)

    litellm.Router.acompletion = _wrapped_router_acompletion
    litellm.Router.completion = _wrapped_router_completion


    # Proxy preprocessing can inject max_output_tokens after Router receives the
    # original kwargs. Strip it at route_request, the final shared boundary.
    import litellm.proxy.route_llm_request as _route_module
    _orig_route_request = _route_module.route_request
    async def _wrapped_route_request(data, *args, **kwargs):
        model = str(data.get("model", ""))
        if _is_codex_model(model):
            _strip_codex_output_limits(data)
        # The /v1/responses route arrives here with `input` and no `messages`. The
        # litellm.aresponses patch does not catch it (the Router captures the
        # function in __init__ by another path), but this is the boundary common to
        # every route: flagging here makes litellm translate
        # Responses -> chat completions e entrar nos bridges.
        if "messages" not in data and data.get("input") is not None:
            if _needs_completions_bridge(model):
                data["use_chat_completions_api"] = True
                # This route's stream must be returned as a CustomStreamWrapper.
                _RESPONSES_ROUTE.set(True)
        return await _orig_route_request(data, *args, **kwargs)
    _route_module.route_request = _wrapped_route_request
    try:
        import litellm.proxy.common_request_processing as _common_processing
        _common_processing.route_request = _wrapped_route_request
    except Exception as _route_patch_error:
        print(f"[sitecustomize] route_request patch skipped: {_route_patch_error}", file=sys.stderr)

    # Assign on litellm's main modules
    litellm.acompletion = _wrapped_acompletion
    litellm.main.acompletion = _wrapped_acompletion
    litellm.completion = _wrapped_completion
    litellm.main.completion = _wrapped_completion

    # --- 4b. Rota /v1/responses ---
    # omp talks to LiteLLM over openai-responses for OpenAI-backed models (to keep
    # the reasoning summaries), and that route goes through neither acompletion
    # nor Router.acompletion: it went straight upstream with the wrong token.
    # Measured before this patch:
    #   codex/gpt-*  -> 401 "Missing scopes: api.responses.write"
    #   gemini-*     -> 401 "Incorrect API key provided: ya29...."  (token
    #                   Google OAuth token sent to api.openai.com)
    #   claude-*     -> 200 but with no reasoning items (the hook never ran)
    # `use_chat_completions_api=True` makes litellm translate
    # Responses -> chat completions and call litellm.acompletion, which the
    # bridges already cover; the reverse translation returns `reasoning` items
    # from `reasoning_content`
    # (LiteLLMCompletionResponsesConfig._extract_reasoning_output_items).
    def _responses_model_of(args, kwargs):
        if "model" in kwargs:
            return str(kwargs.get("model") or "")
        # aresponses(input, model, ...) posicional
        return str(args[1]) if len(args) > 1 else ""

    def _needs_completions_bridge(model):
        lowered = model.lower()
        return (
            _is_gemini_model(lowered)
            or _is_codex_model(lowered)
            or "claude" in lowered
            or "anthropic" in lowered
        )

    try:
        import litellm.responses.main as _responses_module
        _orig_aresponses = _responses_module.aresponses
        _orig_responses = _responses_module.responses

        @functools.wraps(_orig_aresponses)
        async def _wrapped_aresponses(*args, **kwargs):
            if _needs_completions_bridge(_responses_model_of(args, kwargs)):
                kwargs["use_chat_completions_api"] = True
            return await _orig_aresponses(*args, **kwargs)

        @functools.wraps(_orig_responses)
        def _wrapped_responses(*args, **kwargs):
            if _needs_completions_bridge(_responses_model_of(args, kwargs)):
                kwargs["use_chat_completions_api"] = True
            return _orig_responses(*args, **kwargs)

        _responses_module.aresponses = _wrapped_aresponses
        _responses_module.responses = _wrapped_responses
        litellm.aresponses = _wrapped_aresponses
        # `litellm.responses` is deliberately left alone: that name is the
        # litellm/responses/ subpackage, and shadowing it breaks `import
        # litellm.responses.main` elsewhere in the process. The sync function is
        # reached through partial(responses, ...) inside aresponses, which resolves
        # the module global replaced above.
        print("[sitecustomize] /v1/responses route wired to the bridges", file=sys.stderr)
    except Exception as _responses_patch_error:
        print(f"[sitecustomize] responses patch skipped: {_responses_patch_error}", file=sys.stderr)


    # --- 4c. Health checks for wildcard entries ---
    # ahealth_check_wildcard_models picks what to probe with
    # pick_cheapest_chat_models_from_llm_provider, i.e. the cheapest entries in
    # litellm's public price map. For provider "openai" that yields
    # `container` plus `gpt-5-nano*` fallbacks, none of which a subscription
    # account serves -- and since gpt-*, gpt* and gemini-* are declared with
    # custom_llm_provider: openai, all three probed OpenAI models and returned
    # 503. They now probe a model the account serves, chosen from the pattern
    # itself.
    _WILDCARD_HEALTH_PROBE = (
        ("gemini", "gemini-2.5-flash"),
        ("claude", "claude-haiku-4-5"),
        ("gpt", "gpt-5.5"),
    )

    try:
        from litellm.litellm_core_utils import health_check_helpers as _hc_module
        _orig_wildcard_health = _hc_module.HealthCheckHelpers.ahealth_check_wildcard_models

        async def _wrapped_wildcard_health(
            model, custom_llm_provider, model_params, litellm_logging_obj
        ):
            pattern = str(model).lower()
            probe = next((m for marker, m in _WILDCARD_HEALTH_PROBE if marker in pattern), None)
            if probe is None:
                return await _orig_wildcard_health(
                    model=model,
                    custom_llm_provider=custom_llm_provider,
                    model_params=model_params,
                    litellm_logging_obj=litellm_logging_obj,
                )
            params = dict(model_params)
            params["model"] = probe
            params["litellm_logging_obj"] = litellm_logging_obj
            params["fallbacks"] = None
            params.setdefault("max_tokens", 16)
            await litellm.acompletion(**params)
            return {}

        _hc_module.HealthCheckHelpers.ahealth_check_wildcard_models = staticmethod(
            _wrapped_wildcard_health
        )
        print("[sitecustomize] wildcard health checks now probe served models", file=sys.stderr)
    except Exception as _hc_patch_error:
        print(f"[sitecustomize] wildcard health patch skipped: {_hc_patch_error}", file=sys.stderr)

    # --- 5. LiteLLM custom callback that keeps Anthropic caching at proxy level ---
    try:
        from litellm.integrations.custom_logger import CustomLogger
        class AnthropicCacheHandler(CustomLogger):
            def __init__(self):
                pass
            async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
                try:
                    model = str(data.get("model", "")).lower()
                    if "claude" in model or "anthropic" in model:
                        _inject_claude_prompt(data)
                    elif _is_codex_model(model):
                        _strip_codex_output_limits(data)
                except Exception as e:
                    print(f"[AnthropicCacheHandler] Hook error: {e}", file=sys.stderr)
                return data

        _anthropic_cache_logger = AnthropicCacheHandler()
        if _anthropic_cache_logger not in litellm.callbacks:
            litellm.callbacks.append(_anthropic_cache_logger)
        print("[sitecustomize] AnthropicCacheHandler registered in litellm.callbacks", file=sys.stderr)
    except Exception as _cb_err:
        print(f"[sitecustomize] Warning registering the Anthropic callback: {_cb_err}", file=sys.stderr)
    print("[sitecustomize] litellm Claude Code + OpenAI Codex + Google Antigravity bridges enabled", file=sys.stderr)
except Exception as _e:
    print(f"[sitecustomize] Failed to load the litellm bridges: {_e}", file=sys.stderr)
