import { createFileRoute } from "@tanstack/react-router";
import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { ClientOnly } from "@/components/gis/ClientOnly";
import { RouteMap, type Pin } from "@/components/map/RouteMap";
import { DestinationField } from "@/components/plan/DestinationField";
import { ResultPanel } from "@/components/plan/ResultPanel";
import { optimizeRoute, useNetwork, useWarehouses } from "@/lib/api";
import type { OptimizeResponse, Plan } from "@/lib/types";

export const Route = createFileRoute("/")({ component: PlanPage });

function PlanPage() {
  const { data: network, error: networkError, isLoading } = useNetwork();
  const { data: warehouses = [] } = useWarehouses();

  const [destination, setDestination] = useState<Pin | null>(null);
  const [quantity, setQuantity] = useState(500);
  const [deadline, setDeadline] = useState(8);
  const [showHazard, setShowHazard] = useState(false);
  const [selected, setSelected] = useState<Plan | null>(null);
  const [submittedDeadline, setSubmittedDeadline] = useState(deadline);

  const run = useMutation({
    mutationFn: optimizeRoute,
    onSuccess: (res: OptimizeResponse) => setSelected(res.chosen_plan),
  });
  const result = run.data;

  // A changed destination invalidates the previous answer.
  useEffect(() => {
    run.reset();
    setSelected(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [destination?.lat, destination?.lng]);

  const valid = destination && quantity > 0 && deadline > 0;

  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="order-2 w-full shrink-0 overflow-y-auto border-t border-border bg-card md:order-1 md:w-[380px] md:border-r md:border-t-0">
        <form
          className="space-y-4 p-4 md:p-5"
          onSubmit={(e) => {
            e.preventDefault();
            if (!destination) return;
            setSubmittedDeadline(deadline);
            run.mutate({
              destination_lat: destination.lat,
              destination_lng: destination.lng,
              destination_name: destination.name,
              quantity,
              deadline_hours: deadline,
            });
          }}
        >
          <div>
            <h1 className="text-base font-semibold">Plan a delivery</h1>
            <p className="text-sm text-muted-foreground">
              Where, how many, and by when. We handle warehouses and routing.
            </p>
          </div>

          <Field label="Destination">
            <DestinationField value={destination} onChange={setDestination} />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Quantity (units)">
              <NumberInput value={quantity} onChange={setQuantity} min={1} step={1} />
            </Field>
            <Field label="Deadline (hours)">
              <NumberInput value={deadline} onChange={setDeadline} min={0.1} step={0.1} />
            </Field>
          </div>

          <button
            type="submit"
            disabled={!valid || run.isPending}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {run.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            {run.isPending ? "Finding the best route…" : "Find best route"}
          </button>
        </form>

        <div className="space-y-4 px-4 pb-6 md:px-5">
          {run.error && (
            <p
              role="alert"
              className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
            >
              {run.error.message}
            </p>
          )}
          {result && (
            <ResultPanel
              result={result}
              deadline={submittedDeadline}
              warehouses={warehouses}
              selected={selected}
              onSelect={setSelected}
            />
          )}
          {!result && !run.isPending && !run.error && warehouses.length === 0 && (
            <p className="rounded-lg bg-muted p-3 text-sm text-muted-foreground">
              No warehouses are registered yet. Add your inventory (see the Inventory page) to get
              route recommendations.
            </p>
          )}
        </div>
      </aside>

      <main className="relative order-1 h-[48vh] min-h-0 flex-1 md:order-2 md:h-auto">
        {networkError ? (
          <div className="grid size-full place-items-center p-8 text-center text-sm text-muted-foreground">
            Couldn't load the road network: {networkError.message}
          </div>
        ) : (
          <ClientOnly
            fallback={
              <div className="grid size-full place-items-center text-sm text-muted-foreground">
                Loading map…
              </div>
            }
          >
            <RouteMap
              network={network}
              warehouses={warehouses}
              destination={destination}
              plan={selected}
              showHazard={showHazard}
              onPickDestination={(lat, lng) =>
                setDestination({
                  name: `Pinned location`,
                  lat: Number(lat.toFixed(5)),
                  lng: Number(lng.toFixed(5)),
                })
              }
            />
          </ClientOnly>
        )}
        {isLoading && (
          <div className="pointer-events-none absolute left-4 top-4 rounded-md bg-card px-3 py-1.5 text-xs shadow">
            Loading road network…
          </div>
        )}
        <label className="absolute bottom-4 left-4 z-[500] flex cursor-pointer items-center gap-2 rounded-lg border border-border bg-card/95 px-3 py-2 text-xs shadow-sm backdrop-blur">
          <input
            type="checkbox"
            checked={showHazard}
            onChange={(e) => setShowHazard(e.target.checked)}
            className="size-3.5 accent-[var(--primary)]"
          />
          Colour roads by hazard
          {showHazard && (
            <span className="ml-1 flex items-center gap-1" aria-hidden>
              {["#4f9d69", "#d9a02b", "#e0702d", "#c7382f"].map((c) => (
                <span key={c} className="size-2.5 rounded-sm" style={{ background: c }} />
              ))}
            </span>
          )}
        </label>
      </main>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function NumberInput({
  value,
  onChange,
  min,
  step,
}: {
  value: number;
  onChange: (n: number) => void;
  min: number;
  step: number;
}) {
  return (
    <input
      type="number"
      value={Number.isFinite(value) ? value : ""}
      min={min}
      step={step}
      onChange={(e) => onChange(e.target.valueAsNumber)}
      className="w-full rounded-lg border border-input bg-card px-3 py-2.5 text-sm tabular-nums outline-none focus:border-ring focus:ring-2 focus:ring-ring/20"
    />
  );
}
