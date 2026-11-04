import { Link } from "@tanstack/react-router";
import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";
import { useMeta } from "@/lib/api";

const NAV = [
  { to: "/", label: "Plan" },
  { to: "/inventory", label: "Inventory" },
  { to: "/data", label: "Data" },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const { data: meta, error } = useMeta();
  return (
    <div className="flex h-dvh flex-col bg-background text-foreground">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card px-3 md:gap-6 md:px-6">
        <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <svg viewBox="0 0 32 32" className="size-6 text-primary" aria-hidden>
            <path d="M2 26 L11 10 L17 20 L21 14 L30 26 Z" fill="currentColor" />
          </svg>
          <span className="hidden sm:inline">MargaAI</span>{" "}
          <span className="hidden font-normal text-muted-foreground sm:inline">Nepal</span>
        </Link>
        <nav className="flex gap-1 text-sm" aria-label="Main">
          {NAV.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              className="whitespace-nowrap rounded-md px-2 py-1.5 text-muted-foreground md:px-3 transition-colors hover:bg-muted hover:text-foreground"
              activeProps={{
                className: "bg-accent text-accent-foreground font-medium hover:bg-accent",
              }}
              activeOptions={{ exact: true }}
            >
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto hidden text-xs text-muted-foreground sm:block">
          {error ? (
            <span className="text-destructive">Backend offline</span>
          ) : meta ? (
            <span>
              {meta.network.counts.edges.toLocaleString()} road segments ·{" "}
              {meta.network.network_km.toLocaleString()} km ·{" "}
              {meta.weather.available ? "live rainfall" : "no live rainfall"}
            </span>
          ) : null}
        </div>
      </header>
      {meta?.network.is_fixture && (
        <div
          role="alert"
          className="flex items-center gap-2 bg-warning/20 px-4 py-1.5 text-xs font-medium"
        >
          <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
          Development fixture — roads are synthetic, not real. Build the real network (see
          docs/DATA_SOURCES.md) before use.
        </div>
      )}
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}
