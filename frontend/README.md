# MargaAI frontend

TanStack Start with React 19, Tailwind v4 and Leaflet. The browser talks only to this server's `/api/*` gateway, which
forwards to the FastAPI backend at `GIS_API_BASE` (default `http://localhost:8000`).

```bash
cp .env.example .env && npm ci
npm run dev                                          # http://localhost:3000
NITRO_PRESET=node-server npm run build               # production server in .output/, used by the Dockerfile
npx tsc --noEmit && npm run lint
BASE_URL=http://localhost:3000 AI_E2E=1 node e2e/smoke.mjs   # browser smoke test with real sign-in; see the script header
```

Set `AI_E2E=1` to include the AI flows. The pages are Plan (`/`), Inventory, Orders, Team, Data and Account, behind sign-in (`/login`). The
Lovable template builds for Cloudflare by default, so containers need `NITRO_PRESET=node-server`.
