import { createFileRoute } from "@tanstack/react-router";

/**
 * Same-origin gateway to the FastAPI backend.
 *
 * The browser only ever talks to `/api/*` on this server, so no CORS setup is
 * needed and the backend can sit on a private network. Only the read endpoints
 * and the optimiser are forwarded: warehouse writes (admin key) are deliberately
 * unreachable from the public site.
 */
const ALLOWED: Record<string, string[]> = {
  GET: ["/health", "/meta", "/network", "/places", "/warehouses"],
  POST: ["/optimize-route"],
};

function backendBase(): string {
  return (process.env["GIS_API_BASE"] ?? "http://localhost:8000").replace(/\/$/, "");
}

async function forward(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/api/, "") || "/";
  if (!ALLOWED[request.method]?.includes(path)) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  try {
    const upstream = await fetch(`${backendBase()}${path}${url.search}`, {
      method: request.method,
      headers: { "content-type": request.headers.get("content-type") ?? "application/json" },
      body: request.method === "POST" ? await request.text() : null,
      signal: AbortSignal.timeout(60_000),
    });
    const headers = new Headers();
    headers.set("content-type", upstream.headers.get("content-type") ?? "application/json");
    if (request.method === "GET" && path === "/network")
      headers.set("cache-control", "public, max-age=300");
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    return Response.json({ detail: `Backend unreachable (${reason})` }, { status: 502 });
  }
}

export const Route = createFileRoute("/api/$")({
  server: {
    handlers: {
      GET: ({ request }) => forward(request),
      POST: ({ request }) => forward(request),
      PUT: ({ request }) => forward(request),
      PATCH: ({ request }) => forward(request),
      DELETE: ({ request }) => forward(request),
    },
  },
});
