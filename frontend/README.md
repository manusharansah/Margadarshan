# MargaAI frontend

TanStack Start (React 19) + Tailwind v4 + Leaflet. Talks only to its own `/api/*` gateway, which forwards to the FastAPI
backend at `GIS_API_BASE` (default `http://localhost:8000`).

```bash
cp .env.example .env && npm ci
npm run dev                                   # http://localhost:3000
NITRO_PRESET=node-server npm run build        # production server in .output/ (Docker uses this)
npx tsc --noEmit && npm run lint
BASE_URL=http://localhost:3000 node e2e/smoke.mjs   # full-site browser smoke test (needs backend + playwright)
```

Pages: **Plan** (`/`), **Inventory** (`/inventory`), **Data & methods** (`/data`). The default build target of the
Lovable template is Cloudflare; set `NITRO_PRESET=node-server` for AWS/containers.
