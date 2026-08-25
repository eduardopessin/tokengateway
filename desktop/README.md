# Quota Desktop — Local OAuth Loopback Bridge

A lightweight, cross-platform system-tray application built with **Tauri v2 (Rust + TypeScript/HTML)** designed to capture OAuth 2.0 PKCE redirects on local ports and sync credentials with the **Quota Dashboard** and **LiteLLM Gateway**.

---

## Why is a Desktop Client Required?

First-party AI subscription providers hardcode their OAuth redirect URIs to strict, non-configurable `localhost` ports:

| Provider | Service | Required Loopback Port | Redirect URI |
|---|---|---|---|
| **Anthropic** | Claude Code CLI | `54545` | `http://localhost:54545/callback` |
| **OpenAI** | ChatGPT Plus / Codex | `1455` | `http://localhost:1455/auth/callback` |
| **Google** | Antigravity / Cloud Code | `51121` | `http://localhost:51121/oauth-callback` |

Because a remote Kubernetes cluster or cloud homelab server cannot receive HTTP connections directed to your local machine's `127.0.0.1` loopback interface, **Quota Desktop** runs locally on your workstation (Windows, Linux, or macOS) to:
1. Bind local TCP listeners on ports `54545`, `1455`, and `51121`.
2. Intercept the OAuth authorization code when your browser redirects upon successful login.
3. Perform the PKCE code exchange with upstream provider token endpoints.
4. Securely post the resulting credentials to your remote **Quota Dashboard** (`POST /api/credentials`).

---

## Building from Source

### Prerequisites
- [Rust](https://www.rust-lang.org/) (stable)
- [Node.js](https://nodejs.org/) (v18+) or [Bun](https://bun.sh/)
- OS-specific webview libraries:
  - **Windows**: Microsoft Edge WebView2 (pre-installed on Windows 10/11)
  - **Linux**: `sudo apt-get install -y libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf`
  - **macOS**: Xcode Command Line Tools

### Development Mode

```bash
# 1. Install frontend dependencies
npm install

# 2. Start Tauri development mode
npm run tauri dev
```

### Production Build

```bash
npm run tauri build
```

Compiled binaries will be generated under `src-tauri/target/release/` (or `.exe` on Windows).
