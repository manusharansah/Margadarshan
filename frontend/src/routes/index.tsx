import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useServerFn } from "@tanstack/react-start";
import { useMemo, useState } from "react";
import { ClientOnly } from "@/components/gis/ClientOnly";
import { NetworkMap } from "@/components/gis/NetworkMap";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { getNetwork, optimizeRoute } from "@/lib/gis/gis.functions";
import { nodeLabel, placeName } from "@/lib/gis/geo";
import type { OptimizeResponse, PlanSummary } from "@/lib/gis/types";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "MargaAI Nepal — GIS Delivery Route Optimizer" },
      {
        name: "description",
        content:
          "Terrain- and hazard-aware delivery route optimization across Nepal's warehouse network, using Yen's K-shortest paths with deterministic feasibility checks.",
      },
      { property: "og:title", content: "MargaAI Nepal — GIS Delivery Route Optimizer" },
      {
        property: "og:description",
        content:
          "Pick a destination, quantity and deadline; the engine scans every warehouse, routes over terrain and hazard-weighted roads, and explains the recommended plan.",
      },
    ],
  }),
  component: RouteFinder,
});

const PLAN_TYPE_LABEL: Record<string, string> = {
  single_source: "Single warehouse",
  multi_source_parallel: "Multiple warehouses, parallel",
  single_vehicle_multistop: "One vehicle, multi-stop pickup",
};

function RouteFinder() {
  const runOptimize = useServerFn(optimizeRoute);

  const { data: network, isLoading: networkLoading, error: networkError } = useQuery({
    queryKey: ["gis-network"],
    queryFn: () => getNetwork(),
  });

  const [destination, setDestination] = useState("");
  const [quantity, setQuantity] = useState(1000);
  const [deadline, setDeadline] = useState(6);
  const [result, setResult] = useState<OptimizeResponse | null>(null);
  const [selectedPlan, setSelectedPlan] = useState<PlanSummary | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [layers, setLayers] = useState({ roads: true, hazard: true, storages: true });
  const [showAll, setShowAll] = useState(false);
  const [explainOpen, setExplainOpen] = useState(false);

  const nodes = network?.nodes ?? [];
  const edges = network?.edges ?? [];

  const sortedNodes = useMemo(
    () => [...nodes].sort((a, b) => a.node_id.localeCompare(b.node_id, undefined, { numeric: true })),
    [nodes],
  );

  const totals = useMemo(
    () => ({
      stock: nodes.reduce((s, n) => s + n.units_in_stock, 0),
      vehicles: nodes.reduce((s, n) => s + n.vehicles_available, 0),
    }),
    [nodes],
  );

  // The engine can emit hundreds of feasible combinations; show the
  // fastest handful, ranked, so the panel stays readable.
  const alternatives = useMemo(() => {
    const all = result?.rejected_plans ?? result?.candidate_plans ?? [];
    // Combinations that include a warehouse contributing zero units collapse
    // to the same set of legs, so dedupe on the effective source set.
    const seen = new Set<string>();
    const unique = all.filter((p) => {
      const key = [...p.sources_used].sort().join("+");
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    return [...unique]
      .sort(
        (a, b) =>
          a.sources_used.length - b.sources_used.length ||
          a.total_time_hours - b.total_time_hours ||
          a.total_edge_cost - b.total_edge_cost,
      )
      .slice(0, 12);
  }, [result]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!destination) return;
    setRunning(true);
    setError(null);
    try {
      const res = await runOptimize({
        data: { destination, quantity, deadline_hours: deadline },
      });
      setResult(res);
      setSelectedPlan(res.chosen_plan);
      setShowAll(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Route optimization failed.");
      setResult(null);
      setSelectedPlan(null);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background font-sans text-foreground">
      <TopBar backend={network?.backend} />

      <div className="flex min-h-0 flex-1">
        {/* ------------------------------------------------ request panel */}
        <aside className="w-[360px] shrink-0 overflow-y-auto bg-panel px-5 py-5 text-panel-foreground">
          <h2 className="mb-4 text-[15px] font-semibold tracking-tight">New Shipment</h2>

          <form onSubmit={handleSubmit} className="space-y-3">
            <Field label="Destination (delivery node)">
              <select
                value={destination}
                onChange={(e) => setDestination(e.target.value)}
                required
                className="w-full appearance-none bg-transparent text-[15px] text-panel-foreground outline-none"
              >
                <option value="" disabled className="text-black">
                  {networkLoading ? "Loading network…" : "Select a destination"}
                </option>
                {sortedNodes.map((n) => (
                  <option key={n.node_id} value={n.node_id} className="text-black">
                    {nodeLabel(n.node_id)}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Quantity required (units)">
              <input
                type="number"
                min={1}
                step={1}
                value={quantity}
                onChange={(e) => setQuantity(Number(e.target.value))}
                required
                className="w-full bg-transparent text-[15px] text-panel-foreground outline-none"
              />
            </Field>

            <Field label="Delivery deadline (hours)">
              <input
                type="number"
                min={0.5}
                step={0.5}
                value={deadline}
                onChange={(e) => setDeadline(Number(e.target.value))}
                required
                className="w-full bg-transparent text-[15px] text-panel-foreground outline-none"
              />
            </Field>

            <button
              type="submit"
              disabled={running || !destination}
              className="flex w-full items-center justify-between rounded-md bg-panel-foreground px-4 py-3 text-[15px] font-medium text-panel transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {running ? "Computing routes…" : "Find Best Route"}
              <span aria-hidden>→</span>
            </button>
          </form>

          {error && (
            <p className="mt-4 rounded-md border border-panel-border px-3 py-2 text-[13px] text-hazard">
              {error}
            </p>
          )}

          {result?.status === "infeasible" && (
            <div className="mt-5 rounded-lg border border-panel-border p-4">
              <p className="text-[13px] font-semibold uppercase tracking-wide text-hazard">
                Cannot be fulfilled
              </p>
              <p className="mt-2 text-[13px] leading-relaxed text-panel-muted">
                {result.explanation}
              </p>
            </div>
          )}

          {result?.chosen_plan && (
            <>
              <div className="mt-5 rounded-lg border border-panel-border p-4">
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="text-[15px] font-semibold">Top Recommendation</h3>
                  <button
                    onClick={() => setExplainOpen(true)}
                    className="rounded-md border border-panel-border px-2.5 py-1 text-[12px] font-medium text-panel-foreground hover:bg-[var(--panel-border)]"
                  >
                    Why this plan?
                  </button>
                </div>

                <div className="rounded-md border border-panel-border px-3 py-3">
                  <p className="text-[14px] font-medium">
                    {PLAN_TYPE_LABEL[result.chosen_plan.plan_type] ?? result.chosen_plan.plan_type}
                  </p>
                  <p className="mt-1 text-[12px] text-panel-muted">
                    {result.chosen_plan.sources_used.map(placeName).join(" + ")} →{" "}
                    {placeName(destination)}
                  </p>
                </div>

                <div className="mt-3 grid grid-cols-3 divide-x divide-[var(--panel-border)] border-y border-panel-border">
                  <Metric label="ETA" value={`${result.chosen_plan.total_time_hours.toFixed(2)} h`} />
                  <Metric label="Vehicles" value={String(result.chosen_plan.vehicles_used)} />
                  <Metric
                    label="Fuel est."
                    value={`${result.chosen_plan.fuel_estimate.toFixed(1)} L`}
                  />
                </div>

                <div className="mt-3 grid grid-cols-2 gap-3 text-[12px] text-panel-muted">
                  <span>Units: {result.chosen_plan.total_units}</span>
                  <span>Route cost: {result.chosen_plan.total_edge_cost.toFixed(1)}</span>
                  <span>Deadline: {deadline} h</span>
                  <span>Sources: {result.chosen_plan.sources_used.length}</span>
                </div>
              </div>

              {alternatives.length > 0 && (
                <div className="mt-4">
                  <h4 className="mb-2 text-[13px] font-semibold">Other Feasible Plans</h4>
                  <ul className="space-y-1">
                    {(showAll ? alternatives : alternatives.slice(0, 3)).map((plan, i) => {
                      const active = selectedPlan === plan;
                      return (
                        <li key={i}>
                          <button
                            onClick={() => setSelectedPlan(active ? result.chosen_plan : plan)}
                            className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-[13px] transition-colors ${
                              active
                                ? "bg-panel-foreground text-panel"
                                : "text-panel-muted hover:bg-[var(--panel-border)]"
                            }`}
                          >
                            <span className="truncate">
                              {i + 1}. {plan.sources_used.map(placeName).join(" + ")}
                            </span>
                            <span className="ml-2 shrink-0 tabular-nums">
                              {plan.total_time_hours.toFixed(2)} h
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                  {alternatives.length > 3 && (
                    <button
                      onClick={() => setShowAll((v) => !v)}
                      className="mt-2 w-full rounded-md border border-panel-border px-3 py-2 text-[13px] text-panel-muted hover:text-panel-foreground"
                    >
                      {showAll ? "Show fewer" : `View all ${alternatives.length} ranked options`}
                    </button>
                  )}
                </div>
              )}
            </>
          )}

          {!result && !networkLoading && (
            <div className="mt-5 space-y-2 rounded-lg border border-panel-border p-4 text-[12px] text-panel-muted">
              <p className="text-[13px] font-medium text-panel-foreground">Network loaded</p>
              <p>
                {nodes.length} storage nodes · {edges.length} road segments
              </p>
              <p>
                {totals.stock.toLocaleString()} units in stock · {totals.vehicles} vehicles ·
                200 units/vehicle
              </p>
            </div>
          )}

          {/* ------------------------------------------------- map layers */}
          <div className="mt-5 rounded-lg border border-panel-border p-4">
            <h4 className="mb-2 text-[13px] font-semibold">Map Layers</h4>
            {(
              [
                ["roads", "Road network"],
                ["hazard", "Hazard exposure"],
                ["storages", "Storage nodes"],
              ] as const
            ).map(([key, label]) => (
              <label
                key={key}
                className="flex cursor-pointer items-center gap-2 py-1 text-[13px] text-panel-muted"
              >
                <input
                  type="checkbox"
                  checked={layers[key]}
                  onChange={(e) => setLayers((l) => ({ ...l, [key]: e.target.checked }))}
                  className="size-3.5 accent-panel-foreground"
                />
                {label}
              </label>
            ))}
          </div>
        </aside>

        {/* ------------------------------------------------------- map */}
        <main className="relative min-h-0 flex-1">
          {networkError ? (
            <div className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground">
              Could not load the road network: {(networkError as Error).message}
            </div>
          ) : (
            <ClientOnly
              fallback={
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                  Loading map…
                </div>
              }
            >
              <NetworkMap
                nodes={nodes}
                edges={edges}
                destination={destination || null}
                plan={selectedPlan}
                layers={layers}
              />
            </ClientOnly>
          )}
        </main>
      </div>

      <footer className="flex items-center gap-2 bg-panel px-5 py-2 text-[12px] text-panel-muted">
        <span aria-hidden>ⓘ</span>
        All network, inventory and hazard data is simulated for hackathon demonstration purposes.
        Routing uses Yen's K-shortest paths; all quantities, times and fuel figures are computed
        deterministically before any reasoning step.
      </footer>

      {/* --------------------------------------------------- explanation dialog */}
      <Dialog open={explainOpen} onOpenChange={setExplainOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Why this plan?</DialogTitle>
          </DialogHeader>

          {result?.chosen_plan && (
            <div className="space-y-4">
              <div>
                <p className="text-[14px] font-medium">
                  {PLAN_TYPE_LABEL[result.chosen_plan.plan_type] ?? result.chosen_plan.plan_type}
                </p>
                <p className="mt-1 text-[13px] text-muted-foreground">
                  {result.chosen_plan.sources_used.map(placeName).join(" + ")} →{" "}
                  {placeName(destination)}
                </p>
              </div>

              <div className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-3">
                <Factor
                  label="Fulfillment type"
                  value={PLAN_TYPE_LABEL[result.chosen_plan.plan_type] ?? "—"}
                  hint="Simplest sufficient option, per priority order"
                />
                <Factor
                  label="Sources used"
                  value={String(result.chosen_plan.sources_used.length)}
                  hint="Fewest warehouses that satisfy the order"
                />
                <Factor
                  label="Arrival"
                  value={`${result.chosen_plan.total_time_hours.toFixed(2)} h`}
                  hint={`Within the ${deadline} h deadline`}
                />
                <Factor
                  label="Route cost"
                  value={result.chosen_plan.total_edge_cost.toFixed(1)}
                  hint="Distance, slope, road type, hazard"
                />
                <Factor
                  label="Fuel estimate"
                  value={`${result.chosen_plan.fuel_estimate.toFixed(1)} L`}
                  hint="Placeholder proxy formula"
                />
              </div>

              {result.chosen_plan.legs && result.chosen_plan.legs.length > 0 && (
                <div>
                  <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Route legs
                  </p>
                  <ul className="space-y-1.5">
                    {result.chosen_plan.legs.map((leg, i) => (
                      <li key={i} className="rounded-md border border-border px-3 py-2 text-[13px]">
                        <span className="font-medium">{placeName(leg.source)}</span>
                        <span className="text-muted-foreground">
                          {" "}
                          · {leg.units_carried} units · {leg.vehicles_used} vehicle(s) ·{" "}
                          {leg.time_hours.toFixed(2)} h
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="border-t border-border pt-4">
                <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {result.reasoner_used === "live_gemma" ? "Gemma reasoning" : "Selection reasoning"}
                </p>
                <p className="text-[13px] leading-relaxed text-foreground">{result.explanation}</p>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ------------------------------------------------------------ pieces --- */

function TopBar({ backend }: { backend?: string | undefined }) {
  return (
    <header className="flex shrink-0 items-center justify-between bg-panel px-5 py-3 text-panel-foreground">
      <div className="flex items-center gap-3">
        <svg viewBox="0 0 32 32" className="size-8" aria-hidden>
          <path d="M2 26 L11 10 L17 20 L21 14 L30 26 Z" fill="currentColor" opacity="0.9" />
          <path d="M11 10 L14 15.5 L11 20 L8 15.5 Z" fill="#1c1c1a" opacity="0.35" />
        </svg>
        <p className="text-[15px] font-semibold">MargaAI Nepal</p>
      </div>
      <span className="text-[12px] text-panel-muted">
        {backend === "live_fastapi" ? "Live FastAPI backend" : "Local engine"}
      </span>
    </header>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block rounded-md border border-panel-border px-3 pt-2 pb-2.5">
      <span className="mb-0.5 block text-[11px] text-panel-muted">{label}</span>
      {children}
    </label>
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
