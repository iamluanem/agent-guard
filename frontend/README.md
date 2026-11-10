# AgentGuard dashboard

Next.js (App Router) + React + TypeScript + Tailwind CSS v4.

The browser talks only to `/api/ag/*`, a backend-for-frontend route that forwards to the AgentGuard API and injects
`AG_ADMIN_API_KEY` server-side, so the operator key never reaches the browser. Live updates arrive over Server-Sent
Events from the API's transactional outbox (`/v1/stream`).

```bash
npm install
AG_API_URL=http://localhost:8000 AG_ADMIN_API_KEY=<same as backend, if set> npm run dev
```

| Screen | Backed by |
|---|---|
| Overview | `/v1/metrics/overview`, `/v1/demo/scenarios/{n}`, `/v1/stream` |
| Live transactions | `/v1/transactions`, approve / reject |
| Protection policies | `/v1/mandates`, `/v1/intents/extract`, revoke |
| Incidents | `/v1/incidents/{id}` |
| Recovery center | `/v1/recovery`, `/v1/recovery/run-due` |
| Claims | `/v1/claims`, review, `/v1/policies/protection`, `/v1/ledger` |
| Audit trail | `/v1/audit`, `/v1/audit/verify` |
