import { createFileRoute } from "@tanstack/react-router";

/**
 * Same-origin gateway to the FastAPI backend.
 *
 * The browser only talks to `/api/*` on this server, so the session cookie is first-party and no CORS setup is
 * needed. The gateway forwards only known routes (the backend enforces sign-in, roles and tenancy again), passes the
 * session cookie and the CSRF header through, and relays redirects and Set-Cookie untouched so the sign-in flow works.
 */
const ROUTES: Record<string, RegExp[]> = {
  GET: [
    /^\/(health|meta|network|places|warehouses|orders|closures|settings|team|audit|me\/export)$/,
    /^\/auth\/(login|callback|me)$/,
  ],
  POST: [
    /^\/(optimize-route|parse-order|closures|warehouses\/import|team\/invites|auth\/logout)$/,
    /^\/optimize-route\/[a-f0-9]{32}\/ai$/,
  ],
  PUT: [/^\/(settings|warehouses\/[^/]{1,64})$/, /^\/team\/members\/[0-9a-f-]{36}\/role$/],
  DELETE: [
    /^\/(me|org)$/,
    /^\/warehouses\/[^/]{1,64}$/,
    /^\/(closures|team\/invites|team\/members)\/[0-9a-f-]{36}$/,
  ],
};
const SLOW = /^\/(optimize-route\/[a-f0-9]{32}\/ai|parse-order)$/; // model calls can take over a minute
const FORWARD_REQUEST = [
  "cookie",
  "content-type",
  "x-requested-with",
  "origin",
  "x-forwarded-for",
  "accept-language",
];

function backendBase(): string {
  return (process.env["GIS_API_BASE"] ?? "http://localhost:8000").replace(/\/$/, "");
}

async function forward(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/api/, "") || "/";
  if (!ROUTES[request.method]?.some((re) => re.test(path))) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  const headers = new Headers();
  for (const name of FORWARD_REQUEST) {
    const v = request.headers.get(name);
    if (v) headers.set(name, v);
  }
  try {
    const hasBody = request.method !== "GET";
    const upstream = await fetch(`${backendBase()}${path}${url.search}`, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : null,
      redirect: "manual", // the sign-in flow depends on seeing 302s and their Set-Cookie
      signal: AbortSignal.timeout(SLOW.test(path) ? 180_000 : 60_000),
    });
    const out = new Headers();
    for (const name of ["content-type", "location", "cache-control"]) {
      const v = upstream.headers.get(name);
      if (v) out.set(name, v);
    }
    for (const cookie of upstream.headers.getSetCookie()) out.append("set-cookie", cookie);
    return new Response(upstream.body, { status: upstream.status, headers: out });
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    return Response.json({ detail: `Backend unreachable (${reason})` }, { status: 502 });
  }
}

const handler = ({ request }: { request: Request }) => forward(request);

export const Route = createFileRoute("/api/$")({
  server: {
    handlers: { GET: handler, POST: handler, PUT: handler, PATCH: handler, DELETE: handler },
  },
});
