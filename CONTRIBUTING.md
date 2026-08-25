# Contributing to LLM Quota Dashboard & Gateway

Thank you for your interest in contributing to LLM Quota Dashboard & Gateway!

## Development Setup

### 1. Dashboard (Bun / TypeScript)

```bash
cd dashboard
bun install
bun run dev
```

The web dashboard will start at `http://localhost:3737`.

### 2. Quota Desktop (Tauri v2 / Rust + Vite)

Prerequisites:
- [Rust](https://www.rust-lang.org/)
- [Node.js](https://nodejs.org/) or [Bun](https://bun.sh/)
- [Tauri CLI v2](https://v2.tauri.app/)

```bash
cd desktop
npm install
npm run tauri dev
```

### 3. LiteLLM Gateway & Plugin

```bash
docker-compose up -d
```

## Pull Request Guidelines

1. Ensure all code compiles cleanly without TypeScript or Rust errors.
2. Follow existing code formatting and naming conventions.
3. Test streaming and tool calling against mock or live endpoints.
4. Submit your pull request with a descriptive title and summary of changes.
