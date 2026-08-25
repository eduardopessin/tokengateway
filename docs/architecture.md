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
