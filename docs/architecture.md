# TokenGateway — Architecture & Wire Protocols

This document details the system architecture of **TokenGateway** and how it integrates with upstream AI subscription providers.

```
┌─────────────────────────────────────────────────────────────┐
│                    Quota Desktop (Client)                   │
│   (Rust Tauri v2 + OAuth PKCE Loopback on 54545/1455/51121) │
└──────────────────────────────┬──────────────────────────────┘
                               │ POST /api/credentials
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                   Quota Dashboard Server                    │
│   (Bun / TypeScript + Real-time Usage Gauges + Web UI)      │
└──────────────────────────────┬──────────────────────────────┘
                               │
               ┌───────────────┴───────────────┐
               │ Syncs K8s Secrets via RBAC     │
               ▼                               ▼
  Secret/quota-dashboard-creds      Secret/litellm-secrets
                                               │
                                               ▼
┌─────────────────────────────────────────────────────────────┐
│                    LiteLLM Gateway Proxy                    │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ sitecustomize.py (Wire Transformer Plugin)            │  │
│  │                                                       │  │
│  │ • Anthropic: Injects Claude Code system signature     │  │
│  │ • OpenAI: 1:1 Responses API Wire (httpx AsyncStream)  │  │
│  │ • Google: parametersJsonSchema + thoughtSignature     │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Quota Dashboard (Web & Backend)
- Built on **Bun** and **TypeScript** with zero heavy runtime overhead.
- Queries upstream usage endpoints in real time:
  - Anthropic: `/api/oauth/usage` (5-hour and 7-day windows)
  - OpenAI: `chatgpt.com/backend-api/wham/usage`
  - Google: `daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels`
- Tracks local vLLM hardware metrics (VRAM, KV cache, request queue).

### 2. Quota Desktop (Tauri v2 App / Local Loopback Bridge)
- **The Loopback Port Problem**: OAuth 2.0 PKCE providers enforce hardcoded redirect URIs to localhost ports:
  - **Anthropic**: `http://localhost:54545/callback` (port `54545`)
  - **OpenAI**: `http://localhost:1455/auth/callback` (port `1455`)
  - **Google**: `http://localhost:51121/oauth-callback` (port `51121`)
- **The Bridge Function**: When the developer completes OAuth sign-in on their local browser (Windows, Linux, or macOS), the browser redirects to `localhost:<PORT>`. Quota Desktop runs in the local system tray, catches the TCP callback on the required port, completes the PKCE code exchange with the upstream provider, and pushes the resulting credentials to the remote **Quota Dashboard** (`POST /api/credentials`).
- Once received by the Quota Dashboard, credentials are automatically synchronized to Kubernetes Secrets (`quota-dashboard-credentials` and `litellm-secrets`) via cross-namespace RBAC.
### 3. LiteLLM Wire Bridge (`sitecustomize.py`)
- Sits inside the LiteLLM container without modifying the image binary.
- Translates standard OpenAI Chat Completion calls into provider-native wire protocols with full streaming and tool calling support.

#### Reasoning visibility

Reasoning text is the part of a subscription response that is easiest to lose, and
every loss looks like "the model just doesn't show its thinking":

- **`redact-thinking-2026-02-12` must not be in `anthropic-beta`.** With that beta,
  Anthropic returns `thinking` blocks that are signed but carry no text. Measured on
  `claude-sonnet-4-6`: 74 characters of reasoning without the beta, 0 with it. The beta
  list here is omp's `buildCoworkBetas`, which also leaves out `context-1m-2025-08-07`
  because it returns credit `429`s on subscription tokens.
- **Claude 4.7+ and the 5.x line only reason in adaptive mode**, and Anthropic's default
  for `thinking.display` is `"omitted"` — a signed, empty block. The bridge sends
  `thinking: {type: "adaptive", display: "summarized"}` plus
  `output_config: {effort}`. Measured on `claude-opus-5`: 225 characters with
  `summarized`, 0 with `omitted`. Models up to 4.6 keep
  `thinking: {type: "enabled", budget_tokens: N}`.
- **Codex only returns reasoning when the request carries a `reasoning` object.**
  Without it the stream contains zero `response.reasoning_summary_text.delta` events,
  so the bridge always sends one (default effort `medium`) and honours the client's
  `summary` (`auto` | `detailed` | `concise`).
- **Antigravity needs `thinkingConfig` on every request.** Omitting it makes Cloud Code
  Assist re-apply its server defaults and bill thinking tokens without returning the
  text. Antigravity uses *budget* transport (the `thinkingLevel` dialect belongs to
  gemini-cli), so the budget advertised by the account catalogue for the chosen variant
  is used: `-low` 1000, `-medium` 4000, `-high` dynamic, `gemini-pro-agent` 10001.
  `thinkingLevel: MINIMAL` never goes on the wire — `gemini-3.8-*` and
  `gemini-3.1-pro` answer `400 Thinking level MINIMAL is not supported for this model`.

Reasoning reaches the client in `choices[].message.reasoning_content` on chat
completions and as `reasoning` output items on `/v1/responses`. A client that only
renders `content` needs `merge_reasoning_content_in_choices: true` on the model entry,
which wraps the reasoning in `<think>...</think>`.

#### The `/v1/responses` route

Clients that follow LiteLLM's own model discovery — omp among them — call OpenAI-backed
models over `openai-responses` precisely to keep reasoning summaries. That route reaches
neither `litellm.acompletion` nor `Router.acompletion`, so before this was wired it went
straight upstream with the wrong credential:

| model | before |
|---|---|
| `codex`, `gpt-*` | `401 Missing scopes: api.responses.write` |
| `gemini-*` | `401 Incorrect API key provided: ya29....` — the Google OAuth token sent to `api.openai.com` |
| `claude-*` | `200`, but with no `reasoning` items |

Patching `litellm.aresponses` is not enough: the Router captures the function during its
own `__init__` by another path. The boundary common to every route is
`litellm.proxy.route_llm_request.route_request`, where a request carrying `input` and no
`messages` is flagged with `use_chat_completions_api=True`. LiteLLM then translates
Responses into chat completions, calls `litellm.acompletion` — which the bridges already
cover — and rebuilds the `reasoning` items from `reasoning_content`.

Two details make that translation work:

- On that route `reasoning` arrives as an **object** (`{effort, summary}`) and LiteLLM
  forwards the whole object as `reasoning_effort`. Treating it as a string put
  `"{'effort': 'medium', ...}"` on the wire, which Codex rejects with `400 Invalid
  value`.
- Streaming replies must be a `CustomStreamWrapper`; a bare async generator answers
  `500 Unexpected response type: <class 'async_generator'>`. And the final usage chunk
  must carry a `choices` entry: the iterator calls `_is_reasoning_end(chunk)`, which
  dereferences `chunk.choices[0]` unguarded, so `choices: []` raised `IndexError` and the
  stream died before emitting `response.completed` — leaving clients waiting forever on
  exactly the models that stream reasoning.

#### Wildcards and silent substitution

Per-family wildcards (`claude-*`, `gpt-*`, `gemini-*`) are what let a newly released
model work without editing the config, and the effort variant is resolved from the
account catalogue. What they must not do is answer with a different model:

- An arbitrary name is only remapped when it is a **deliberate alias** of a served
  model. `gpt-4.1`, `o3-mini` and `gpt-3.5-turbo` were being answered by `gpt-5.5` with
  the `model` field echoing the requested name, so billing and comparisons lied.
- An unknown gemini family raises instead of falling back to `gemini-2.5-flash`.
- A bare `- model_name: "*"` catch-all is worse still: every typo is answered by whatever
  backend it points at. Keep it out of the config.

Health checks on wildcard entries need help, because
`ahealth_check_wildcard_models` probes the cheapest models in LiteLLM's public price map
(`container`, `gpt-5-nano*` for provider `openai`) and a subscription serves none of
them. The probe is chosen from the pattern instead: `gemini-*` → `gemini-2.5-flash`,
`claude-*` → `claude-haiku-4-5`, `gpt-*` → `gpt-5.5`.

#### Deployment identity

Pin `model_info.id` on every entry. Without it the Router derives the id in
`_generate_model_id` as a sha256 over every key and value of `litellm_params`: the id is
a fingerprint of the parameters, so it changes whenever any of them changes and is
recomputed on every restart. Anything addressing a deployment by id — `GET
/health?model_id=`, the admin UI, dashboards — then answers `404 Model with ID ... not
found`. Wildcard entries additionally materialise per-request deployments
(`original_model_id` is set on those), which live only in the router's memory and
disappear on restart, so health-checking those by id is unstable by construction; use
`?model=<name>`.

#### Prompt Caching Breakpoints (Anthropic)

Anthropic caches everything **up to** a `cache_control` marker, so where the marker goes
decides how much of the request is a cache hit. The placement here is ported 1:1 from the
`@oh-my-pi/pi-ai` transformers:

- **Nothing is marked on `system`.** Anchoring the end of the conversation already covers
  tools + system + the whole history.
- **The last two markable turns are anchored, not one.** Two adjacent anchors guarantee a
  valid entry to extend from once the conversation grows by another turn.
- **Reasoning blocks are never anchors** (`thinking`, `redacted_thinking`, `fallback`), and
  marking stops if a block already carries `cache_control`.
- **Retention defaults to a bare ephemeral marker (5 min).** A `ttl: "1h"` marker costs 2x
  base to write against 1.25x for 5 min, so the long TTL is opt-in rather than automatic.

Because the bridge receives the OpenAI shape, a tool result arrives as its own
`role: "tool"` message and an assistant tool call carries `content: None`. Rewriting either
into a text block breaks the conversion LiteLLM performs downstream, so neither is ever a
candidate for a marker.

Measured on a repeated 11,543-token conversation: a single marker on the static prefix left
`cache_read` pinned at 3,853 tokens regardless of conversation size — the cached share
*decayed* as the session grew. Anchoring the tail moved it to 11,541 (99.98%).

#### Usage Accounting

The bridges answer requests themselves, so anything they fail to extract is lost: LiteLLM
falls back to `token_counter` estimates (`prompt_tokens or token_counter(...)`) and every
cache hit stays invisible in `/spend/logs`. The arithmetic differs per provider, and the
asymmetry is intentional — it mirrors the reference implementation:

| Provider | `input` | `output` | `cacheRead` |
|---|---|---|---|
| **Google** | `promptTokenCount − cachedContentTokenCount` (the field *includes* cached tokens) | `candidatesTokenCount + thoughtsTokenCount` | `cachedContentTokenCount` |
| **OpenAI Codex** | `input_tokens` (**not** reduced) | `output_tokens` | `input_tokens_details.cached_tokens`, falling back to `prompt_cache_hit_tokens` |

Both streaming generators emit a final chunk carrying `usage` so `stream_chunk_builder` uses
upstream truth instead of estimating. Without it, streaming responses — which is what coding
agents send — are logged as guesses.

#### Reloading the plugin on Kubernetes

Mounting `sitecustomize.py` from a ConfigMap does **not** make a running process re-read it:
the module stays imported in memory. Unless you run something like Stakater Reloader, the
pod template itself has to change, so put a real hash of the ConfigMaps in the Deployment
annotation and recompute it on every edit:

```yaml
annotations:
  # sha256 of the mounted ConfigMaps, recomputed whenever either changes.
  checksum/config: "<sha256>"
```

A static placeholder silently keeps the old code running, and `kubectl rollout status`
still reports success because there is no rollout pending. Confirm a deploy by checking for
a **new pod name**, not by the rollout command.
