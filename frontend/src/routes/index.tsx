import { createFileRoute } from "@tanstack/react-router";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { ClientOnly } from "@/components/gis/ClientOnly";
import { RouteMap, type Pin } from "@/components/map/RouteMap";
import { DestinationField } from "@/components/plan/DestinationField";
import { ResultPanel } from "@/components/plan/ResultPanel";
import { optimizeRoute, useNetwork, useWarehouses } from "@/lib/api";
import { formatInt } from "@/lib/format";
import type { OptimizeResponse, Plan } from "@/lib/types";

export const Route = createFileRoute("/")({
  head: () => ({ meta: [{ title: "MargaAI Nepal — Delivery Route Optimizer" }] }),
  component: PlanPage,
});

function PlanPage() {
  const { data: network, error: networkError, isLoading } = useNetwork();
  const { data: warehouses = [] } = useWarehouses();

  const [destination, setDestination] = useState<Pin | null>(null);
  const [quantity, setQuantity] = useState(1000);
  const [deadline, setDeadline] = useState(6);
  const [layers, setLayers] = useState({ roads: true, hazard: true, storages: true });
  const [selected, setSelected] = useState<Plan | null>(null);
  const [submitted, setSubmitted] = useState({ deadline: 6, name: "" });

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

  const totals = useMemo(
    () => ({
      stock: warehouses.reduce((s, w) => s + w.units_in_stock, 0),
      vehicles: warehouses.reduce((s, w) => s + w.vehicles_available, 0),
    }),
    [warehouses],
  );

  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="order-2 w-full shrink-0 overflow-y-auto bg-panel px-5 py-5 text-panel-foreground md:order-1 md:w-[360px]">
        <h2 className="mb-4 text-[15px] font-semibold tracking-tight">New Shipment</h2>

        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (!destination) return;
            setSubmitted({ deadline, name: destination.name });
            run.mutate({
              destination_lat: destination.lat,
              destination_lng: destination.lng,
              destination_name: destination.name,
              quantity,
              deadline_hours: deadline,
            });
          }}
        >
          <DestinationField value={destination} onChange={setDestination} />

          <Field label="Quantity required (units)">
            <NumberInput value={quantity} onChange={setQuantity} min={1} step={1} />
          </Field>
          <Field label="Delivery deadline (hours)">
            <NumberInput value={deadline} onChange={setDeadline} min={0.1} step={0.1} />
          </Field>

          <button
            type="submit"
            disabled={run.isPending || !destination || !(quantity > 0) || !(deadline > 0)}
            className="flex w-full items-center justify-between rounded-md bg-panel-foreground px-4 py-3 text-[15px] font-medium text-panel transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {run.isPending ? "Computing routes…" : "Find Best Route"}
            <span aria-hidden>→</span>
          </button>
        </form>

        {run.error && (
          <p
            role="alert"
            className="mt-4 rounded-md border border-panel-border px-3 py-2 text-[13px] text-hazard"
          >
            {run.error.message}
          </p>
        )}

        {result && (
          <ResultPanel
            result={result}
            deadline={submitted.deadline}
            destinationName={submitted.name}
            warehouses={warehouses}
            selected={selected}
            onSelect={setSelected}
          />
        )}

        {!result && !isLoading && (
          <div className="mt-5 space-y-2 rounded-lg border border-panel-border p-4 text-[12px] text-panel-muted">
            <p className="text-[13px] font-medium text-panel-foreground">Network loaded</p>
            <p>
              {warehouses.length} storage nodes · {network?.features.length.toLocaleString() ?? 0}{" "}
              road segments
            </p>
            <p>
              {formatInt(totals.stock)} units in stock · {totals.vehicles} vehicles
            </p>
            {warehouses.length === 0 && <p>No warehouses yet — see the Inventory page.</p>}
          </div>
        )}

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

      <main className="relative order-1 h-[48vh] min-h-0 flex-1 md:order-2 md:h-auto">
        {networkError ? (
          <div className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground">
            Could not load the road network: {networkError.message}
          </div>
        ) : (
          <ClientOnly
            fallback={
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                Loading map…
              </div>
            }
          >
            <RouteMap
              network={network}
              warehouses={warehouses}
              destination={destination}
              plan={selected}
              layers={layers}
              onPickDestination={(lat, lng) =>
                setDestination({
                  name: "Pinned location",
                  lat: Number(lat.toFixed(5)),
                  lng: Number(lng.toFixed(5)),
                })
              }
            />
          </ClientOnly>
        )}
      </main>
    </div>
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
      required
      className="w-full bg-transparent text-[15px] text-panel-foreground outline-none"
    />
  );
}
