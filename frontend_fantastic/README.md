# AgentFactor Fantastic-admin Dashboard

This directory contains the Fantastic-admin based dashboard for AgentFactor.

## Local Development

Install Node.js and pnpm in your local shell, WSL environment, or preferred Node version manager.

Run the dashboard dev server:

```bash
cd frontend_fantastic
pnpm install
pnpm dev
```

For production builds:

```bash
cd frontend_fantastic
pnpm install
pnpm build
```

The development server proxies API calls to `http://127.0.0.1:9889`.

## Backend Mount

FastAPI serves the built SPA from:

```text
frontend_fantastic/apps/core/dist
```

After `pnpm build`, open:

```text
http://127.0.0.1:9889/admin
```
