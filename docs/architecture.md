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
