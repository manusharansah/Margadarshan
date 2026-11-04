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
    <div className="flex h-dvh flex-col overflow-hidden bg-background font-sans text-foreground">
      <header className="flex shrink-0 items-center gap-6 bg-panel px-5 py-3 text-panel-foreground">
        <Link to="/" className="flex items-center gap-3">
          <svg viewBox="0 0 32 32" className="size-8" aria-hidden>
            <path d="M2 26 L11 10 L17 20 L21 14 L30 26 Z" fill="currentColor" opacity="0.9" />
            <path d="M11 10 L14 15.5 L11 20 L8 15.5 Z" fill="#1c1c1a" opacity="0.35" />
          </svg>
          <span className="text-[15px] font-semibold">MargaAI Nepal</span>
        </Link>
        <nav className="flex gap-1 text-[13px]" aria-label="Main">
          {NAV.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              className="rounded-md px-3 py-1.5 text-panel-muted transition-colors hover:text-panel-foreground"
              activeProps={{ className: "bg-panel-border text-panel-foreground" }}
              activeOptions={{ exact: true }}
            >
              {n.label}
            </Link>
          ))}
        </nav>
        <span className="ml-auto hidden text-[12px] text-panel-muted sm:block">
          {error
            ? "Backend offline"
            : meta
              ? `${meta.network.counts.edges.toLocaleString()} road segments · ${meta.weather.available ? "live rainfall" : "no live rainfall"}`
              : ""}
        </span>
      </header>

      {meta?.network.is_fixture && (
        <div
          role="alert"
          className="flex shrink-0 items-center gap-2 bg-warning px-5 py-1.5 text-[12px] font-medium text-panel"
        >
          <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
          Development fixture — roads are synthetic, not real. Build the real network
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
