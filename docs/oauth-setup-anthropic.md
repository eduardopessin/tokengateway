# Anthropic Claude Max OAuth Setup Guide

This guide explains how Quota Dashboard and LiteLLM authenticate with Anthropic Claude Max / Pro subscriptions using OAuth PKCE.

## How it works

Anthropic utilizes OAuth 2.0 PKCE to authenticate CLI tools (like Claude Code). When you authorize:
1. An authorization code is captured via local loopback port `54545`.
2. An initial `access_token` (~8-hour lifetime) and a rotating single-use `refresh_token` are granted.
3. The LiteLLM wire bridge makes the Claude Agent SDK identity the **first** block of `system` and appends the client's instructions as a second block. What OAuth requires is the identity coming first, not being alone: measured with a subscription token, `system=[identity]` returns `200`, `system=[identity, client]` returns `200` and the client instruction is obeyed on `claude-opus-5`, `claude-sonnet-4-6`, `claude-opus-4-8` and `claude-opus-4-6`, while `system=[client]` alone returns `429`.

## Authentication via Quota Desktop

1. Open **Quota Desktop**.
2. Click **Connect** under the **Anthropic** card.
3. Your default web browser will open to `claude.ai/oauth/authorize`.
4. Sign in with your Anthropic subscription account and approve the client.
5. The desktop client automatically catches the callback on `http://localhost:54545/callback`, exchanges the code, and syncs credentials to the dashboard.

## Manual Authentication via Terminal (Oh My Pi / OMP)

If you already use OMP or Claude CLI, your OAuth token is saved locally. Export it with:

```bash
# The exact path depends on your installation; check your OMP config directory.
# Example: sqlite3 ~/.omp/agent/agent.db "SELECT data FROM auth_credentials WHERE provider='anthropic'"
```

> **Note:** Never share or commit the exported token values.
