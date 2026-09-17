# OpenAI ChatGPT Plus / Codex Responses API Setup Guide

This guide explains how Quota Dashboard and LiteLLM connect to the official OpenAI ChatGPT Plus / Codex Responses API.

## How it works

OpenAI provides an internal Responses API endpoint (`https://chatgpt.com/backend-api/codex/responses`) for coding agents.
- **Client ID**: see `.env.example` / `OPENAI_CODEX_CLIENT_ID`
- **Loopback Callback Port**: `1455` (`/auth/callback`)
- **Protocol**: OpenAI Responses API wire protocol (JSON/SSE)

## Authentication via Quota Desktop

1. Open **Quota Desktop**.
2. Click **Connect** under the **OpenAI Codex** card.
3. Your browser will open to `auth.openai.com/oauth/authorize`.
4. Sign in with your ChatGPT Plus / Pro account.
5. The desktop client captures the code, extracts the `chatgpt_account_id` and plan type from the JWT, and syncs credentials.

## Supported Models in LiteLLM

Once authenticated, LiteLLM routes the following model IDs to ChatGPT Plus with full function calling and streaming:
- `gpt-5.6-terra`
- `gpt-5.6-sol`
- `gpt-5.6-luna`
- `gpt-6-astra`
- `gpt-5`
- `codex`

`gpt-5`, `gpt-6` and `codex` are family aliases: they do not name a version, so
resolving them to the served one is honest. Version-specific names are not
aliased. The `gpt-5.4` family is refused by a ChatGPT account — *"The 'gpt-5.4'
model is not supported when using Codex with a ChatGPT account"* — so those
names are not served at all rather than quietly answered by `gpt-5.5`, which
would bill and log the client against a model that never ran.
