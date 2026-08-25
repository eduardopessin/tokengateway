# Google Antigravity (Cloud Code API) Setup Guide

This guide explains how Quota Dashboard and LiteLLM connect to Google's internal Cloud Code / Antigravity API for Gemini models.

## How it works

Google Antigravity utilizes the internal Cloud Code API (`https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse`) to provide high-tier access to Gemini models.
- **Client ID**: see `.env.example` / `GOOGLE_CLIENT_ID`
- **Loopback Callback Port**: `51121` (`/oauth-callback`)
- **Key Features**: OpenAPI 3.0 schema support via `parametersJsonSchema`, `thoughtSignature` preservation, and extended 64k token outputs.

## Authentication via Quota Desktop

1. Open **Quota Desktop**.
2. Click **Connect** under **Google Antigravity**.
3. Sign in to your Google Account.
4. Quota Desktop captures the authorization code and exchanges it for an offline refresh token.

## Supported Models in LiteLLM

- `gemini-3.7-flash`
- `gemini-3.7-flash-thinking`
- `gemini-3.7-flash-tiered`
- `gemini-2.5-pro`
- `gemini-2.5-flash`
- `gemini-3.1-pro`
