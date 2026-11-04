import { AlertTriangle, CheckCircle2, ChevronDown, Info, XCircle } from "lucide-react";
import { useState } from "react";
import { ElevationProfile } from "./ElevationProfile";
import {
  PLAN_LABEL,
  formatHours,
  formatInt,
  formatKm,
  formatLitres,
  hazardLevel,
} from "@/lib/format";
import type { OptimizeResponse, Plan, Warehouse } from "@/lib/types";

interface Props {
  result: OptimizeResponse;
  deadline: number;
  warehouses: Warehouse[];
  selected: Plan | null;
  onSelect: (plan: Plan) => void;
}

export function ResultPanel({ result, deadline, warehouses, selected, onSelect }: Props) {
  const nameOf = (id: string) =>
    id
      .split("+")
      .map((p) => warehouses.find((w) => w.node_id === p)?.name ?? p)
      .join(" + ");

  if (result.status === "infeasible" || !result.chosen_plan) {
    const suggested =
      result.best_case_hours != null ? Math.ceil(result.best_case_hours * 2) / 2 : null;
    return (
      <div className="space-y-3">
        <div className="flex gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <XCircle className="mt-0.5 size-5 shrink-0 text-destructive" aria-hidden />
          <div className="space-y-1.5">
            <p className="font-semibold">Can't be delivered as requested</p>
            <p className="text-sm leading-relaxed text-muted-foreground">{result.explanation}</p>
            {suggested != null && (
              <p className="text-sm">
                Fastest possible: <strong>{formatHours(result.best_case_hours!)}</strong> — try a
                deadline of at least <strong>{suggested} h</strong>.
              </p>
            )}
          </div>
        </div>
        <Notices result={result} />
      </div>
    );
  }

  const plan = selected ?? result.chosen_plan;
  const isTop = plan === result.chosen_plan;
  const hazard = hazardLevel(Math.max(...plan.legs.map((l) => l.max_hazard), 0));
  const margin = deadline - plan.total_time_hours;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 rounded-xl border border-positive/30 bg-positive/5 p-4">
        <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-positive" aria-hidden />
        <div>
          <p className="font-semibold">Arrives in {formatHours(plan.total_time_hours)}</p>
          <p className="text-sm text-muted-foreground">
            {formatHours(margin)} inside the {deadline} h deadline ·{" "}
            {isTop ? "Recommended" : "Alternative"} plan
          </p>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border bg-border text-sm">
        <Stat label="Distance" value={formatKm(plan.distance_km)} />
        <Stat label="Fuel (est.)" value={formatLitres(plan.fuel_litres)} />
        <Stat label="Vehicles" value={String(plan.vehicles_used)} />
        <Stat
          label="Road hazard"
          value={hazard.label}
          dot={hazard.colour}
          hint={`worst stretch ${Math.max(...plan.legs.map((l) => l.max_hazard), 0).toFixed(2)} / 1`}
        />
      </dl>

      <section aria-label="Route">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {PLAN_LABEL[plan.plan_type]}
        </h3>
        <ol className="space-y-1.5">
          {plan.legs.map((leg, i) => (
            <li
              key={i}
              className="flex items-baseline justify-between gap-3 rounded-lg bg-muted/60 px-3 py-2 text-sm"
            >
              <span className="min-w-0 truncate font-medium">{nameOf(leg.source)}</span>
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                {formatInt(leg.units_carried)} units · {formatHours(leg.time_hours)}
              </span>
            </li>
          ))}
        </ol>
      </section>

      <ElevationProfile plan={plan} />

      <section className="rounded-xl border border-border p-4" aria-label="Explanation">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Why this plan
          </h3>
          <SourceBadge source={result.explanation_source} />
        </div>
        <p className="text-sm leading-relaxed">{result.explanation}</p>
        {result.gemma_error && (
          <p className="mt-2 text-xs text-muted-foreground">
            Gemma unavailable, so this summary was generated from the computed figures:{" "}
            {result.gemma_error}
          </p>
        )}
        {result.chosen_matches_policy === false && (
          <p className="mt-2 text-xs text-muted-foreground">
            Gemma preferred this plan over the rule-based top choice.
          </p>
        )}
      </section>

      {result.alternatives.length > 0 && (
        <Alternatives result={result} selected={plan} onSelect={onSelect} nameOf={nameOf} />
      )}
      <Notices result={result} />
    </div>
  );
}

function Alternatives({
  result,
  selected,
  onSelect,
  nameOf,
}: {
  result: OptimizeResponse;
  selected: Plan;
  onSelect: (p: Plan) => void;
  nameOf: (s: string) => string;
}) {
  const [open, setOpen] = useState(false);
  const all = [result.chosen_plan!, ...result.alternatives];
  return (
    <section aria-label="Alternative plans">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted-foreground"
        aria-expanded={open}
      >
        Other feasible plans ({result.alternatives_total})
        <ChevronDown
          className={`size-4 transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>
      {open && (
        <ul className="mt-2 space-y-1">
          {all.map((p, i) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => onSelect(p)}
                className={`flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                  p === selected ? "bg-primary text-primary-foreground" : "hover:bg-muted"
                }`}
              >
                <span className="min-w-0 truncate">{p.sources_used.map(nameOf).join(" + ")}</span>
                <span className="shrink-0 text-xs tabular-nums opacity-80">
                  {formatHours(p.total_time_hours)} · {p.vehicles_used} veh
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Notices({ result }: { result: OptimizeResponse }) {
  const notes: { icon: "warn" | "info"; text: string }[] = result.warnings.map((text) => ({
    icon: "warn",
    text,
  }));
  if (result.destination.snap_distance_m > 500)
    notes.push({
      icon: "info",
      text: `Destination is ${(result.destination.snap_distance_m / 1000).toFixed(1)} km from the nearest mapped road; the route ends at that road.`,
    });
  if (result.weather && !result.weather.available)
    notes.push({
      icon: "info",
      text: "Live rainfall unavailable — hazard uses terrain and incident data only.",
    });
  if (!notes.length) return null;
  return (
    <ul className="space-y-1.5">
      {notes.map((n, i) => (
        <li key={i} className="flex gap-2 text-xs text-muted-foreground">
          {n.icon === "warn" ? (
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
          ) : (
            <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          )}
          <span>{n.text}</span>
        </li>
      ))}
    </ul>
  );
}

function SourceBadge({ source }: { source: "gemma" | "deterministic" }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
        source === "gemma" ? "bg-accent text-accent-foreground" : "bg-muted text-muted-foreground"
      }`}
      title={
        source === "gemma"
          ? "Selected and explained by Gemma"
          : "Rule-based summary of the computed figures"
      }
    >
      {source === "gemma" ? "Gemma" : "Rule-based"}
    </span>
  );
}

function Stat({
  label,
  value,
  hint,
  dot,
}: {
  label: string;
  value: string;
  hint?: string;
  dot?: string;
}) {
  return (
    <div className="bg-card px-3 py-2.5">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 flex items-center gap-1.5 font-semibold tabular-nums">
        {dot && <span className="size-2 rounded-full" style={{ background: dot }} aria-hidden />}
        {value}
      </dd>
      {hint && <p className="text-[10px] text-muted-foreground">{hint}</p>}
    </div>
  );
}
