import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, signOut, useMe, useMeta } from "@/lib/api";
import { ROLE_RANK } from "@/lib/types";

const NAV = [
  { to: "/", label: "Plan" },
  { to: "/inventory", label: "Inventory" },
  { to: "/orders", label: "Orders" },
  { to: "/team", label: "Team" },
  { to: "/data", label: "Data" },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const navigate = useNavigate();
  const { data: me, error: meError, isLoading } = useMe();
  const signedIn = Boolean(me);
  const { data: meta, error } = useMeta(signedIn);
  const unauth = meError instanceof ApiError && meError.status === 401;

  useEffect(() => {
    if (unauth && path !== "/login")
      void navigate({ to: "/login", search: { next: path } as never, replace: true });
  }, [unauth, path, navigate]);

  if (path === "/login")
    return <div className="h-dvh bg-panel text-panel-foreground">{children}</div>;
  if (!me) {
    return (
      <div className="grid h-dvh place-items-center bg-panel text-[13px] text-panel-muted">
        {meError && !unauth
          ? `Cannot reach the service: ${meError.message}`
          : isLoading
            ? "Loading…"
            : "Redirecting to sign in…"}
      </div>
    );
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-background font-sans text-foreground">
      <header className="flex shrink-0 items-center gap-4 bg-panel px-4 py-3 text-panel-foreground md:gap-6 md:px-5">
        <Link to="/" className="flex items-center gap-3">
          <svg viewBox="0 0 32 32" className="size-8" aria-hidden>
            <path d="M2 26 L11 10 L17 20 L21 14 L30 26 Z" fill="currentColor" opacity="0.9" />
            <path d="M11 10 L14 15.5 L11 20 L8 15.5 Z" fill="#1c1c1a" opacity="0.35" />
          </svg>
          <span className="hidden text-[15px] font-semibold sm:inline">MargaAI Nepal</span>
        </Link>
        <nav className="flex gap-1 text-[13px]" aria-label="Main">
          {NAV.filter((n) => n.to !== "/team" || ROLE_RANK[me.role] >= 1).map((n) => (
            <Link
              key={n.to}
              to={n.to}
              className="rounded-md px-2 py-1.5 text-panel-muted transition-colors hover:text-panel-foreground md:px-3"
              activeProps={{ className: "bg-panel-border text-panel-foreground" }}
              activeOptions={{ exact: true }}
            >
              {n.label}
            </Link>
          ))}
        </nav>
        <span className="ml-auto hidden text-[12px] text-panel-muted lg:block">
          {error
            ? "Backend offline"
            : meta
              ? `${meta.network.counts.edges.toLocaleString()} road segments · ${meta.weather.available ? "live rainfall" : "no live rainfall"}`
              : ""}
        </span>
        <UserMenu
          name={me.user.name || me.user.email}
          email={me.user.email}
          org={me.organisation.name}
          role={me.role}
        />
      </header>

      {meta?.network.is_fixture && (
        <div
          role="alert"
          className="flex shrink-0 items-center gap-2 bg-warning px-5 py-1.5 text-[12px] font-medium text-panel"
        >
          <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
          Development fixture: roads are synthetic, not real. Build the real network
          (docs/DATA_SOURCES.md) before use.
        </div>
      )}

      <div className="min-h-0 flex-1">{children}</div>

      <footer className="flex shrink-0 items-center gap-2 bg-panel px-5 py-2 text-[12px] text-panel-muted">
        <span aria-hidden>ⓘ</span>
        <span className="truncate">
          {meta
            ? `Roads: ${meta.network.sources.roads} · Elevation: SRTM · Rainfall: ${meta.weather.available ? "Open-Meteo" : "unavailable"}. `
            : ""}
          All quantities, times and fuel figures are computed in code before any reasoning step.
        </span>
      </footer>
    </div>
  );
}

function UserMenu({
  name,
  email,
  org,
  role,
}: {
  name: string;
  email: string;
  org: string;
  role: string;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  useEffect(() => {
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const initials = name
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("");
  return (
    <div ref={box} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        className="grid size-8 place-items-center rounded-full border border-panel-border text-[12px] font-semibold hover:bg-panel-border"
      >
        {initials || "?"}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-[2000] mt-2 w-64 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg"
        >
          <div className="border-b border-border px-3 py-2">
            <p className="truncate text-[13px] font-medium">{name}</p>
            <p className="truncate text-[12px] text-muted-foreground">{email}</p>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {org} · {role}
            </p>
          </div>
          <Link
            to="/account"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="block rounded px-3 py-2 text-[13px] hover:bg-accent"
          >
            Account and data
          </Link>
          <button
            role="menuitem"
            className="block w-full rounded px-3 py-2 text-left text-[13px] hover:bg-accent"
            onClick={async () => {
              await signOut().catch(() => undefined);
              qc.clear();
              window.location.assign("/login");
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
