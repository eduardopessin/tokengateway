<div align="center">

# ⚡ TokenGateway

**Unlock, monitor, and route your paid AI subscriptions (Claude Max, ChatGPT Plus, Google Gemini) into coding agents, LiteLLM, and Kubernetes with 100% native wire protocol fidelity.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Built with Bun](https://img.shields.io/badge/Runtime-Bun-black.svg?logo=bun)](https://bun.sh)
[![Built with Rust & Tauri](https://img.shields.io/badge/Desktop-Tauri_v2-orange.svg?logo=tauri)](https://v2.tauri.app)
[![Kubernetes Ready](https://img.shields.io/badge/Kubernetes-Native-326ce5.svg?logo=kubernetes)](https://kubernetes.io)
[![LiteLLM Compatible](https://img.shields.io/badge/LiteLLM-Proxy_Plugin-brightgreen.svg)](https://litellm.ai)

</div>

---

## 💡 The Problem

Developers and AI engineers pay expensive monthly subscriptions (**Claude Max** \$100–\$200/mo, **ChatGPT Plus/Pro**, **Google AI**), but face massive friction when using them in coding agents (*Oh My Pi*, *Claude Code*, *OpenHands*, *Cline*) or local clusters:
1. **Opaque Usage Limits:** Subscription quotas (Anthropic 5h/7d rolling windows, OpenAI 3h message caps, Google daily quotas) are hidden inside web interfaces.
2. **Missing Wire Protocols:** Upstream gateways (like LiteLLM) often drop tool schemas, mangle message types, or drop chunks when connecting to subscription endpoints.
3. **Rotating Refresh Token Drift:** Anthropic rotates `refresh_token` single-use credentials; multiple processes refreshing tokens cause race conditions and premature auth invalidation (`400 invalid_grant`).

**TokenGateway solves this completely.**

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Quota Desktop (Client)                   │
│   (Rust Tauri v2 + OAuth PKCE Loopback on 54545/1455/51121) │
└──────────────────────────────┬──────────────────────────────┘
                               │ POST /api/credentials
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                   Quota Dashboard Server                    │
│   (Bun / TypeScript + Real-time Usage Gauges + Dark Web UI) │
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
│  │ • Anthropic: Isolates Claude Agent SDK identity + caches client instructions │
│  │ • OpenAI: 1:1 Responses API Wire (httpx AsyncStream)  │  │
│  │ • Google: parametersJsonSchema + thoughtSignature     │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 📸 Screenshots

### 1. Real-Time Subscription & Cluster Quota Dashboard
*Live visual countdown gauges, 5-hour session limits, 7-day rolling utilization, local vLLM VRAM allocation, and autonomous agent status in a sleek dark-mode UI.*

<div align="center">
  <img src="docs/assets/quota-dashboard.png" alt="TokenGateway Quota Dashboard" width="100%" />
</div>

<br/>

### 2. LiteLLM Proxy Unified Model Catalog
*46+ managed models unified under a single OpenAI-compatible endpoint with automatic OAuth wire protocol translation and function calling.*

<div align="center">
  <img src="docs/assets/litellm-models.png" alt="LiteLLM Models Management" width="100%" />
</div>

---

## 🖥️ Why is Quota Desktop Needed? (The Localhost Port Binding Constraint)

When authenticating with first-party AI subscriptions using OAuth 2.0 PKCE, the providers enforce **hardcoded, strictly whitelisted loopback redirect URIs on specific local ports**:

| Provider | Client Signature | Whitelisted Loopback Redirect URI | Port |
|---|---|---|---|
| **Anthropic** | Claude Code CLI | `http://localhost:54545/callback` | `54545` |
| **OpenAI** | ChatGPT / Codex CLI | `http://localhost:1455/auth/callback` | `1455` |
| **Google** | Antigravity / Cloud Code | `http://localhost:51121/oauth-callback` | `51121` |

### The Remote Cluster Dilemma
When you complete OAuth sign-in on your workstation's web browser, the provider redirects your browser to `http://localhost:<PORT>`.

- A remote server or Kubernetes cluster running in your homelab/cloud **cannot listen on your local machine's `localhost` interface**.
- Manual copy-pasting of complex PKCE challenge codes and JWT tokens is error-prone and breaks when `refresh_token`s rotate.

### The Solution: Quota Desktop as a Loopback Bridge
**Quota Desktop** runs locally on your workstation (Windows system tray, macOS, or Linux):
1. Binds lightweight local TCP listeners to ports `54545`, `1455`, and `51121`.
2. Automatically catches the OAuth redirect from your browser in milliseconds.
3. Exchanges the authorization code and PKCE code verifier with the upstream provider.
4. Securely pushes the generated credentials to your remote **Quota Dashboard & LiteLLM Gateway** (`POST /api/credentials`), where they are persisted to Kubernetes Secrets and auto-refreshed 24/7.

---
## ✨ Features

- 📊 **Real-Time Subscription Monitor:** Live countdown gauges and visual consumption fractions for Anthropic (5h/7d/Fable), OpenAI ChatGPT Plus (7d), and Google Antigravity.
- 🔄 **Atomic Token Manager & Dual-Secret Sync:** Thread-safe, single-use token rotation with cross-namespace Kubernetes RBAC (`litellm-secrets` + `quota-dashboard-credentials`).
- 🛠️ **100% OMP / OpenAI Wire Protocol Fidelity:**
  - **OpenAI Responses API (`gpt-5.*`, `codex/*`):** Maps `input_text`, `output_text`, `function_call`, `function_call_output` with async streaming.
  - **Google Cloud Code API (`gemini-*`):** Full support for `parametersJsonSchema`, `thoughtSignature` caching/bypass, and extended 64k token outputs.
  - **Anthropic Claude (`claude-*`):** Isolates the required Claude Agent SDK identity in the sole system message; client instructions move to a cached first user block. This preserves prompt-cache hits and avoids OAuth `429` rejections.
- 🖥️ **Cross-Platform Desktop Client (Tauri v2):** Lightweight system-tray application (Windows, Linux, macOS) with local loopback listeners on ports `54545`, `1455`, and `51121`.
- ⚡ **Local Hardware Telemetry:** Real-time VRAM and KV cache monitoring for local vLLM instances (NVIDIA RTX / CUDA).

---

## 🚀 Quick Start (Docker Compose)

Run the dashboard and LiteLLM gateway locally in one command:

```bash
# 1. Clone the repository
git clone https://github.com/eduardopessin/tokengateway.git
cd tokengateway

# 2. Copy environment template
cp .env.example .env

# 3. Start the stack
docker-compose up -d
```

Open `http://localhost:3737` to access the Quota Dashboard.

---

## 📦 Supported Providers & Models

| Provider | Subscriptions | Model IDs in LiteLLM | Tool Calling | Streaming |
|---|---|---|---|---|
| **Anthropic** | Claude Max / Pro | `claude-sonnet-5`, `claude-opus-5`, `claude-haiku-4-5` | ✔️ Full | ✔️ Full |
| **Google** | Google One AI / Antigravity | `gemini-3.7-flash`, `gemini-2.5-pro`, `gemini-2.5-flash` | ✔️ Full | ✔️ Full |
| **OpenAI** | ChatGPT Plus / Pro | `gpt-5.6-terra`, `gpt-5.6-sol`, `gpt-5.4`, `gpt-5.4-mini` | ✔️ Full | ✔️ Full |
| **Local vLLM** | Self-Hosted | `qwen-agent-coder` (Qwen 3.5 27B NVFP4) | ✔️ Full | ✔️ Full |

---

## 📖 Documentation

- [Architecture & Wire Protocols](docs/architecture.md)
- [Anthropic Claude Max Setup Guide](docs/oauth-setup-anthropic.md)
- [OpenAI ChatGPT Plus Setup Guide](docs/oauth-setup-openai.md)
- [Google Antigravity Setup Guide](docs/oauth-setup-google.md)
- [LiteLLM Plugin Details](litellm-plugin/README.md)

## 🙏 Standing on the Shoulders of Giants

This project is deeply inspired by and built to integrate seamlessly with leading open-source AI infrastructure:

- 🚀 **[LiteLLM](https://github.com/BerriAI/litellm)**: The premier multi-model LLM proxy gateway. Our plugin (`sitecustomize.py`) extends LiteLLM's runtime capabilities with non-invasive monkey-patching, adding first-party CLI wire protocol translation without requiring a custom fork.
- ⚡ **[Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi)**: The load-bearing autonomous coding agent harness. The wire transformer logic in this project (`L1t`, `W1t`, `parametersJsonSchema`, and `skip_thought_signature_validator`) is directly modeled after `@oh-my-pi/pi-ai`'s reference transformers to ensure 100% protocol fidelity in real-world multi-turn tool-calling sessions.
- 🤖 **Compatible Coding Agents**: Built and tested for seamless operation with [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi), [Claude Code](https://claude.ai/code), [OpenHands](https://github.com/All-Hands-AI/OpenHands), [Cline](https://github.com/cline/cline), and [Aider](https://github.com/paul-gauthier/aider).

---
## ⚠️ Known Security Issues

This project is a personal homelab tool, not production hardened. The following issues are known and unresolved:

| ID | Severity | Component | Issue |
|---|---|---|---|
| TG-001 | 🔴 Critical | `dashboard/server.ts` | `GET /api/credentials` and `POST /api/credentials` have **no authentication**. Any process on the same host or Docker network can read or overwrite all OAuth tokens. |
| TG-002 | 🔴 Critical | `desktop/src-tauri/src/oauth.rs` | TLS certificate validation is **disabled** (`danger_accept_invalid_certs(true)`) in the Rust HTTP client used to sync credentials to the cluster. Susceptible to MITM on LAN. |
| TG-003 | 🔴 Critical | `litellm-plugin/sitecustomize.py` | The plugin reads the pod's Kubernetes Service Account token at runtime and uses it to PATCH `secrets/litellm-secrets` on every token refresh. Any RCE in the LiteLLM process grants K8s secret-write capability. |
| TG-004 | 🟠 High | `docker-compose.yml` | `LITELLM_MASTER_KEY` falls back to a hardcoded default (`sk-quota-gateway-master-key`) if the env var is unset. |
| TG-005 | 🟠 High | `dashboard/src/store.ts` + `deploy/kubernetes/rbac.yaml` | The dashboard attempts to PATCH a secret in the `litellm` namespace but the declared RBAC `Role` is scoped to `quota-dashboard` only. Either silently fails or implies undeclared cluster permissions. |
| TG-006 | 🟡 Medium | `dashboard/src/oauth.ts` | OIDC `id_token` is decoded without signature verification — identity claims accepted on trust. |
| TG-007 | 🟡 Medium | `dashboard/src/oauth.ts` | OAuth callback listener binds `0.0.0.0` during login — ephemeral port open to all interfaces for up to 15 minutes. |
| TG-008 | 🟡 Medium | `docker-compose.yml` | `credentials.json` is `chmod 0600` but the Docker volume is shared with the LiteLLM container, which can read it if running as root or the same UID. |
| TG-009 | 🟡 Medium | `desktop/src-tauri/src/lib.rs` | Tauri `open_url` command passes arbitrary URLs to the OS browser opener without validating the scheme (`file://`, `javascript:` accepted). |
| TG-010 | 🟡 Medium | `litellm-plugin/sitecustomize.py` | Refreshed OAuth tokens are written back to `os.environ`, exposing them via `/proc/self/environ` to co-tenant processes. |
| TG-011 | 🔵 Low | `desktop/src-tauri/src/oauth.rs` | Google OAuth token exchange does not validate the `state` parameter (CSRF). Anthropic and OpenAI exchanges do. |
| TG-012 | 🟠 High → ✅ Fixed | `litellm-plugin/sitecustomize.py` | `GOOGLE_CLIENT_SECRET` was hardcoded in source and present throughout git history. Now read from the `GOOGLE_CLIENT_SECRET` env var (empty default). History was rewritten to purge the value; **if the secret was project-owned it should still be rotated**, since force-push does not un-expose an already-public value. |
| TG-013 | 🟡 Medium | `desktop/src-tauri/Cargo.lock` | `glib` 0.18.5 (transitive via Tauri 2's gtk-rs 0.18 stack) is affected by GHSA-wrw7-89jp-8q8g (unsoundness in `VariantStrIter` iterators). No isolated fix: the patched 0.20.0 requires migrating the whole gtk-rs line, which stable Tauri 2 does not yet support. The unsound path is not exercised by this app; accepted as tolerable risk until Tauri bumps its GTK bindings. |

**TG-001 is the most immediately exploitable** — no prerequisites, single HTTP request. If you deploy this outside a trusted single-machine environment, add at minimum a shared-secret header check on all `/api/*` routes and bind the dashboard to `127.0.0.1`.

---


## 🤝 Contributing

Contributions are welcome! Please check out [CONTRIBUTING.md](CONTRIBUTING.md) for development instructions.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
