# TradeCoreFX frontend deployment notes

This document describes the current development setup. It does not establish a
production hosting architecture or change backend behavior.

## Local development

1. Start the configured local model service when using Ollama:

   ```bash
   ollama serve
   ```

2. Activate the project's Python environment, install the project
   dependencies as required, and launch the existing FastAPI server:

   ```bash
   vibe-trading serve --port 8899
   ```

3. In a separate terminal, start the Vite frontend:

   ```bash
   cd frontend
   npm run dev -- --host 127.0.0.1 --port 5899
   ```

The Vite development UI is available at `http://127.0.0.1:5899` and proxies
API requests to `http://127.0.0.1:8899` by default. The combined production
server is available at `http://127.0.0.1:8899` after the frontend build has
been created.

## Production build and serving

Build the frontend before starting the existing server that serves its static
assets:

```bash
cd frontend
npm ci
npm run build
cd ..
vibe-trading serve --port 8899
```

The frontend is a browser-router SPA. The production web server must return
the built `index.html` for application paths such as `/agent` and
`/risk-manager`, while leaving API paths (for example `/runs/{id}/code`) routed
to FastAPI. Verify direct navigation and refresh behavior behind any reverse
proxy before release.

`VITE_API_URL` can point Vite's development proxy to a non-default API target.
It is a public build-time value, so never place secrets in it or in any other
`VITE_*` variable. Environment files are excluded from Git; keep operational
secrets in secured server-side environment configuration.

## Access and ports

- **8899**: existing FastAPI application and the built frontend in the combined
  setup.
- **5899**: Vite development server only.
- **API_AUTH_KEY**: set a strong value for non-local access. Users must enter
  the corresponding key in Intelligence Settings for protected browser API
  requests and SSE access.
- Keep development ports private or loopback-bound. Put any public deployment
  behind an HTTPS reverse proxy with explicit trusted origins.

## Current product limitations

- Ollama is a local development dependency. A stronger hosted-model evaluation
  and validation process is required before production market reasoning.
- The contact form is a development preview and does not transmit messages.
- Authentication, subscriptions, persistent journal storage, and a production
  contact endpoint still require implementation.
- No broker execution should be enabled without separate review and approval.

## Production readiness checklist

- Configure secure secret storage, HTTPS, authentication, rate limiting, and
  explicit CORS/trusted-origin policy.
- Provide persistent database storage, backups, logging, monitoring, and an
  incident-response process.
- Confirm market-data licensing and provider terms before any production data
  distribution.
- Add reviewed privacy, terms, cookie, and risk-disclosure pages.
- Validate the hosting architecture, access controls, and model behavior before
  exposing the platform beyond approved users.
