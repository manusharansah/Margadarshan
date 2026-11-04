import { useState } from "react";
import { ElevationProfile } from "./ElevationProfile";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
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
  destinationName: string;
  warehouses: Warehouse[];
  selected: Plan | null;
  onSelect: (plan: Plan) => void;
  onApplyRemedy: (quantity: number, deadline: number) => void;
}

/** Result area of the left panel: verdict card, "Top Recommendation", alternatives, explanation dialog. */
export function ResultPanel({
  result,
  deadline,
  destinationName,
  warehouses,
  selected,
  onSelect,
  onApplyRemedy,
}: Props) {
  const [explainOpen, setExplainOpen] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const nameOf = (id: string) =>
    id
      .split("+")
      .map((p) => warehouses.find((w) => w.node_id === p)?.name ?? p)
      .join(" + ");

  if (result.status === "infeasible" || !result.chosen_plan) {
    const suggested =
      result.best_case_hours != null ? Math.ceil(result.best_case_hours * 2) / 2 : null;
    return (
      <>
        <div className="mt-5 rounded-lg border border-panel-border p-4">
          <p className="text-[13px] font-semibold uppercase tracking-wide text-hazard">
            Cannot be fulfilled
          </p>
          <p className="mt-2 text-[13px] leading-relaxed text-panel-muted">{result.explanation}</p>
          {suggested != null && (
            <p className="mt-2 text-[13px]">
              Fastest possible is {formatHours(result.best_case_hours!)} — try a deadline of at
              least {suggested} h.
            </p>
          )}
        </div>
        {result.remedies.length > 0 && (
          <div className="mt-4" aria-label="Options that would work">
            <h4 className="mb-2 text-[13px] font-semibold">Options that would work</h4>
            <ul className="space-y-2">
              {result.remedies.map((r, i) => (
                <li key={i} className="rounded-md border border-panel-border p-3 text-[13px]">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">
                      {formatInt(r.quantity)} units · {r.deadline_hours} h
                    </span>
                    <span className="rounded-full border border-panel-border px-1.5 py-0.5 text-[10px] font-semibold text-panel-muted">
                      {r.source === "gemma" ? "AI suggestion" : "Computed"}
                    </span>
                  </div>
                  <p className="mt-1 text-[12px] leading-snug text-panel-muted">{r.rationale}</p>
                  <p className="mt-1 text-[11px] text-panel-muted">
                    Verified by the route planner: arrives in {formatHours(r.eta_hours)} via{" "}
                    {r.sources.join(" + ")}.
                  </p>
                  <button
                    onClick={() => onApplyRemedy(r.quantity, r.deadline_hours)}
                    className="mt-2 flex w-full items-center justify-between rounded-md border border-panel-foreground px-3 py-1.5 text-[12px] font-medium hover:bg-panel-foreground hover:text-panel"
                  >
                    Use this option <span aria-hidden>→</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        <Notices result={result} />
      </>
    );
  }

  const plan = selected ?? result.chosen_plan;
  const all = [result.chosen_plan, ...result.alternatives];
  const worst = Math.max(...plan.legs.map((l) => l.max_hazard), 0);
  const hazard = hazardLevel(worst);

  return (
    <>
      <div className="mt-5 rounded-lg border border-panel-border p-4">
        <h3 className="mb-3 text-[15px] font-semibold">
          {plan === result.chosen_plan ? "Top Recommendation" : "Alternative Plan"}
        </h3>

        <div className="rounded-md border border-panel-border px-3 py-3">
          <p className="text-[14px] font-medium">{PLAN_LABEL[plan.plan_type]}</p>
          <p className="mt-1 text-[12px] text-panel-muted">
            {plan.sources_used.map(nameOf).join(" + ")} → {destinationName}
          </p>
        </div>

        <div className="mt-3 grid grid-cols-3 divide-x divide-[var(--panel-border)] border-y border-panel-border">
          <Metric label="ETA" value={formatHours(plan.total_time_hours)} />
          <Metric label="Vehicles" value={String(plan.vehicles_used)} />
          <Metric label="Fuel est." value={formatLitres(plan.fuel_litres)} />
        </div>

        <div className="mt-3 grid grid-cols-2 gap-3 text-[12px] text-panel-muted">
          <span>Units: {formatInt(plan.total_units)}</span>
          <span>Distance: {formatKm(plan.distance_km)}</span>
          <span>Deadline: {deadline} h</span>
          <span className="flex items-center gap-1.5">
            Hazard:{" "}
            <span
              className="size-2 rounded-full"
              style={{ background: hazard.colour }}
              aria-hidden
            />
            {hazard.label}
          </span>
        </div>

        {result.risk_note && (
          <p className="mt-3 text-[12px] leading-snug text-panel-muted" aria-label="Risk briefing">
            <span className="font-semibold text-panel-foreground">Risk briefing</span>
            {result.explanation_source === "gemma" && (
              <span className="ml-1.5 rounded-full border border-panel-border px-1.5 py-0.5 text-[10px]">
                AI
              </span>
            )}
            <br />
            {result.risk_note}
          </p>
        )}

        <button
          onClick={() => setExplainOpen(true)}
          className="mt-3 flex w-full items-center justify-between rounded-md border border-panel-foreground px-4 py-2.5 text-[14px] font-medium text-panel-foreground transition-colors hover:bg-panel-foreground hover:text-panel"
        >
          Why this plan? See full explanation
          <span aria-hidden>→</span>
        </button>
      </div>

      {result.alternatives.length > 0 && (
        <div className="mt-4" aria-label="Alternative plans">
          <h4 className="mb-2 text-[13px] font-semibold">Other Feasible Plans</h4>
          <ul className="space-y-1">
            {(showAll ? all : all.slice(0, 4)).map((p, i) => (
              <li key={i}>
                <button
                  onClick={() => onSelect(p)}
                  className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-[13px] transition-colors ${
                    p === plan
                      ? "bg-panel-foreground text-panel"
                      : "text-panel-muted hover:bg-[var(--panel-border)]"
                  }`}
                >
                  <span className="truncate">
                    {i + 1}. {p.sources_used.map(nameOf).join(" + ")}
                  </span>
                  <span className="ml-2 shrink-0 tabular-nums">
                    {formatHours(p.total_time_hours)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {all.length > 4 && (
            <button
              onClick={() => setShowAll((v) => !v)}
              className="mt-2 w-full rounded-md border border-panel-border px-3 py-2 text-[13px] text-panel-muted hover:text-panel-foreground"
            >
              {showAll ? "Show fewer" : `View all ${all.length} ranked options`}
            </button>
          )}
        </div>
      )}
      <Notices result={result} />

      <Dialog open={explainOpen} onOpenChange={setExplainOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Why this plan?</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <p className="text-[14px] font-medium">{PLAN_LABEL[plan.plan_type]}</p>
              <p className="mt-1 text-[13px] text-muted-foreground">
                {plan.sources_used.map(nameOf).join(" + ")} → {destinationName}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-3">
              <Factor
                label="Arrival"
                value={formatHours(plan.total_time_hours)}
                hint={`Within the ${deadline} h deadline`}
              />
              <Factor
                label="Sources used"
                value={String(plan.sources_used.length)}
                hint="Fewest warehouses that satisfy the order"
              />
              <Factor
                label="Route cost"
                value={plan.total_edge_cost.toFixed(1)}
                hint="Length, surface, grade, hazard"
              />
              <Factor
                label="Distance"
                value={formatKm(plan.distance_km)}
                hint="Along mapped roads"
              />
              <Factor
                label="Fuel estimate"
                value={formatLitres(plan.fuel_litres)}
                hint="Base use + climb energy"
              />
              <Factor
                label="Worst hazard"
                value={`${hazard.label} (${worst.toFixed(2)})`}
                hint="0 = safe, 1 = severe"
              />
            </div>

            <div>
              <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
                Route legs
              </p>
              <ul className="space-y-1.5">
                {plan.legs.map((leg, i) => (
                  <li key={i} className="rounded-md border border-border px-3 py-2 text-[13px]">
                    <span className="font-medium">{nameOf(leg.source)}</span>
                    <span className="text-muted-foreground">
                      {" "}
                      · {formatInt(leg.units_carried)} units · {leg.vehicles_used} vehicle(s) ·{" "}
                      {formatHours(leg.time_hours)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <div>
              <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
                Elevation along the route
              </p>
              <ElevationProfile plan={plan} />
            </div>

            <div className="border-t border-border pt-4">
              <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
                {result.explanation_source === "gemma"
                  ? "Gemma reasoning"
                  : "Selection reasoning (rule-based)"}
              </p>
              <p className="text-[13px] leading-relaxed text-foreground">{result.explanation}</p>
              {result.gemma_error && (
                <p className="mt-2 text-[12px] text-muted-foreground">
                  Gemma unavailable: {result.gemma_error}
                </p>
              )}
              {result.chosen_matches_policy === false && (
                <p className="mt-2 text-[12px] text-muted-foreground">
                  Gemma preferred this plan over the rule-based top choice.
                </p>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

function Notices({ result }: { result: OptimizeResponse }) {
  const notes = [...result.warnings];
  if (result.destination.snap_distance_m > 500)
    notes.push(
      `Destination is ${(result.destination.snap_distance_m / 1000).toFixed(1)} km from the nearest mapped road; the route ends at that road.`,
    );
  if (result.weather && !result.weather.available)
    notes.push("Live rainfall unavailable — hazard uses terrain and incident data only.");
  if (!notes.length) return null;
  return (
    <ul className="mt-4 space-y-1.5">
      {notes.map((n, i) => (
        <li key={i} className="text-[12px] leading-snug text-panel-muted">
          ⓘ {n}
        </li>
      ))}
    </ul>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-2 py-2.5">
      <p className="text-[11px] text-panel-muted">{label}</p>
      <p className="mt-0.5 text-[14px] font-medium tabular-nums">{value}</p>
    </div>
  );
}

function Factor({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div>
      <p className="text-[12px] text-muted-foreground">{label}</p>
      <p className="mt-1 text-[15px] leading-tight font-semibold tabular-nums">{value}</p>
      <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{hint}</p>
    </div>
  );
}
