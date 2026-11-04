import { createFileRoute } from "@tanstack/react-router";
import { useWarehouses } from "@/lib/api";
import { formatInt } from "@/lib/format";

export const Route = createFileRoute("/inventory")({ component: InventoryPage });

function InventoryPage() {
  const { data = [], isLoading, error } = useWarehouses();
  const totals = data.reduce(
    (t, w) => ({
      stock: t.stock + w.units_in_stock,
      vehicles: t.vehicles + w.vehicles_available,
      cap: t.cap + w.vehicles_available * w.vehicle_capacity,
    }),
    { stock: 0, vehicles: 0, cap: 0 },
  );
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-6 p-4 md:p-8">
        <div>
          <h1 className="text-xl font-semibold">Inventory</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Warehouse stock and fleet are your own operational data, kept in the database. This page
            shows what the optimiser will use right now.
          </p>
        </div>

        {error ? (
          <p className="text-sm text-destructive">{error.message}</p>
        ) : isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : data.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border p-8 text-center">
            <p className="font-medium">No warehouses registered</p>
            <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
              Import a CSV with{" "}
              <code className="rounded bg-muted px-1">
                id,name,lat,lng,units_in_stock,vehicles_available
              </code>{" "}
              using{" "}
              <code className="rounded bg-muted px-1">
                python -m gis_backend.seed_warehouses file.csv
              </code>{" "}
              or <code className="rounded bg-muted px-1">POST /warehouses/import</code>.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-border bg-card">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Warehouse</th>
                  <th className="px-4 py-2.5 font-medium">Location</th>
                  <th className="px-4 py-2.5 text-right font-medium">In stock</th>
                  <th className="px-4 py-2.5 text-right font-medium">Vehicles</th>
                  <th className="px-4 py-2.5 text-right font-medium">Max per dispatch</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.map((w) => (
                  <tr key={w.node_id}>
                    <td className="px-4 py-2.5">
                      <span className="font-medium">{w.name}</span>
                      <span className="ml-2 text-xs text-muted-foreground">{w.node_id}</span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground tabular-nums">
                      {w.lat.toFixed(4)}, {w.lng.toFixed(4)}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      {formatInt(w.units_in_stock)}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{w.vehicles_available}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      {formatInt(w.vehicles_available * w.vehicle_capacity)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t border-border bg-muted/30 text-sm font-semibold">
                <tr>
                  <td className="px-4 py-2.5" colSpan={2}>
                    Total
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{formatInt(totals.stock)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{totals.vehicles}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{formatInt(totals.cap)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
