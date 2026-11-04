import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Btn, Card, ErrorNote, Input, Page, Section } from "@/components/ui-bits";
import { send, uploadWarehouses, useClosures, useMe, useSettings, useWarehouses } from "@/lib/api";
import { formatInt } from "@/lib/format";
import { ROLE_RANK, type Warehouse } from "@/lib/types";

export const Route = createFileRoute("/inventory")({ component: InventoryPage });

const EMPTY = {
  node_id: "",
  name: "",
  lat: "",
  lng: "",
  units_in_stock: "0",
  vehicles_available: "0",
  vehicle_capacity: "200",
};

function InventoryPage() {
  const { data: me } = useMe();
  const canEdit = me ? ROLE_RANK[me.role] >= ROLE_RANK.admin : false;
  const { data = [], isLoading, error } = useWarehouses();
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["warehouses"] });
  const [form, setForm] = useState(EMPTY);
  const file = useRef<HTMLInputElement>(null);

  const save = useMutation({
    mutationFn: () =>
      send("PUT", `/warehouses/${encodeURIComponent(form.node_id.trim())}`, {
        name: form.name.trim(),
        lat: Number(form.lat),
        lng: Number(form.lng),
        units_in_stock: Number(form.units_in_stock),
        vehicles_available: Number(form.vehicles_available),
        vehicle_capacity: Number(form.vehicle_capacity),
      }),
    onSuccess: () => {
      setForm(EMPTY);
      void refresh();
    },
  });
  const del = useMutation({
    mutationFn: (id: string) => send("DELETE", `/warehouses/${encodeURIComponent(id)}`),
    onSuccess: () => void refresh(),
  });
  const upload = useMutation({ mutationFn: uploadWarehouses, onSuccess: () => void refresh() });

  const totals = data.reduce(
    (t, w) => ({
      stock: t.stock + w.units_in_stock,
      vehicles: t.vehicles + w.vehicles_available,
      cap: t.cap + w.vehicles_available * w.vehicle_capacity,
    }),
    { stock: 0, vehicles: 0, cap: 0 },
  );
  const edit = (w: Warehouse) =>
    setForm({
      node_id: w.node_id,
      name: w.name,
      lat: String(w.lat),
      lng: String(w.lng),
      units_in_stock: String(w.units_in_stock),
      vehicles_available: String(w.vehicles_available),
      vehicle_capacity: String(w.vehicle_capacity),
    });

  return (
    <Page
      title="Inventory"
      intro="Warehouse stock and fleet are your own operating data. The route planner uses exactly what is listed here, for your organisation only."
    >
      <Section title="Warehouses">
        {error ? (
          <ErrorNote error={error} />
        ) : isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : data.length === 0 ? (
          <Card className="p-8 text-center">
            <p className="font-medium">No warehouses yet</p>
            <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
              {canEdit
                ? "Add one below or upload a CSV with id, name, lat, lng, units_in_stock, vehicles_available."
                : "Ask an admin to add your warehouses."}
            </p>
          </Card>
        ) : (
          <Card>
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Warehouse</th>
                  <th className="px-4 py-2.5 font-medium">Location</th>
                  <th className="px-4 py-2.5 text-right font-medium">In stock</th>
                  <th className="px-4 py-2.5 text-right font-medium">Vehicles</th>
                  <th className="px-4 py-2.5 text-right font-medium">Max per dispatch</th>
                  {canEdit && <th className="px-4 py-2.5" />}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.map((w) => (
                  <tr key={w.node_id}>
                    <td className="px-4 py-2.5">
                      <span className="font-medium">{w.name}</span>
                      <span className="ml-2 text-xs text-muted-foreground">{w.node_id}</span>
                    </td>
                    <td className="px-4 py-2.5 text-xs tabular-nums text-muted-foreground">
                      {w.lat.toFixed(4)}, {w.lng.toFixed(4)}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      {formatInt(w.units_in_stock)}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{w.vehicles_available}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      {formatInt(w.vehicles_available * w.vehicle_capacity)}
                    </td>
                    {canEdit && (
                      <td className="space-x-2 whitespace-nowrap px-4 py-2.5 text-right">
                        <button
                          className="text-xs text-muted-foreground hover:text-foreground"
                          onClick={() => edit(w)}
                        >
                          Edit
                        </button>
                        <button
                          className="text-xs text-destructive"
                          onClick={() => confirm(`Delete ${w.name}?`) && del.mutate(w.node_id)}
                          aria-label={`Delete ${w.name}`}
                        >
                          Delete
                        </button>
                      </td>
                    )}
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
                  {canEdit && <td />}
                </tr>
              </tfoot>
            </table>
          </Card>
        )}
        <ErrorNote error={del.error} />
      </Section>

      {canEdit && (
        <Section
          title="Add or update a warehouse"
          note="Saving an id that exists updates it. The location must be within 5 km of a mapped road."
        >
          <form
            className="grid grid-cols-2 gap-2 md:grid-cols-4"
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate();
            }}
          >
            {(
              [
                ["node_id", "ID", "text"],
                ["name", "Name", "text"],
                ["lat", "Latitude", "number"],
                ["lng", "Longitude", "number"],
                ["units_in_stock", "Units in stock", "number"],
                ["vehicles_available", "Vehicles", "number"],
                ["vehicle_capacity", "Units per vehicle", "number"],
              ] as const
            ).map(([k, label, type]) => (
              <label key={k} className="space-y-1 text-xs text-muted-foreground">
                {label}
                <Input
                  className="w-full"
                  type={type}
                  step={type === "number" ? "any" : undefined}
                  value={form[k]}
                  required
                  onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))}
                />
              </label>
            ))}
            <div className="flex items-end">
              <Btn type="submit" disabled={save.isPending}>
                Save warehouse
              </Btn>
            </div>
          </form>
          <ErrorNote error={save.error} />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <input
              ref={file}
              type="file"
              accept=".csv,text/csv"
              aria-label="Upload CSV"
              className="text-xs"
            />
            <Btn
              kind="ghost"
              disabled={upload.isPending}
              onClick={() => file.current?.files?.[0] && upload.mutate(file.current.files[0])}
            >
              Import CSV
            </Btn>
            {upload.data && (
              <span className="text-xs text-positive">
                Imported {upload.data.imported} warehouses.
              </span>
            )}
          </div>
          <ErrorNote error={upload.error} />
        </Section>
      )}

      <Rules canEdit={canEdit} />
    </Page>
  );
}

function Rules({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const { data: settings } = useSettings();
  const { data: closures = [] } = useClosures();
  const [minutes, setMinutes] = useState<string | null>(null);
  const [c, setC] = useState({ lat: "", lng: "", radius_m: "300", reason: "", until_hours: "" });
  const saveSettings = useMutation({
    mutationFn: () => send("PUT", "/settings", { loading_minutes: Number(minutes) }),
    onSuccess: () => {
      setMinutes(null);
      void qc.invalidateQueries({ queryKey: ["settings"] });
    },
  });
  const addClosure = useMutation({
    mutationFn: () =>
      send("POST", "/closures", {
        lat: Number(c.lat),
        lng: Number(c.lng),
        radius_m: Number(c.radius_m),
        reason: c.reason,
        ...(c.until_hours ? { until_hours: Number(c.until_hours) } : {}),
      }),
    onSuccess: () => {
      setC({ lat: "", lng: "", radius_m: "300", reason: "", until_hours: "" });
      void qc.invalidateQueries({ queryKey: ["closures"] });
    },
  });
  const removeClosure = useMutation({
    mutationFn: (id: string) => send("DELETE", `/closures/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["closures"] }),
  });

  return (
    <>
      <Section
        title="Handling time"
        note="Minutes spent loading at each warehouse stop. It is added to every plan's arrival time. We found no published figure for Nepal, so it starts at zero. Set it from your own timings."
      >
        <div className="flex items-center gap-2">
          <Input
            type="number"
            min={0}
            max={480}
            aria-label="Handling minutes per stop"
            disabled={!canEdit}
            className="w-24"
            value={minutes ?? String(settings?.loading_minutes ?? 0)}
            onChange={(e) => setMinutes(e.target.value)}
          />
          <span className="text-sm text-muted-foreground">minutes per stop</span>
          {canEdit && minutes !== null && (
            <Btn onClick={() => saveSettings.mutate()} disabled={saveSettings.isPending}>
              Save
            </Btn>
          )}
        </div>
        <ErrorNote error={saveSettings.error} />
      </Section>

      <Section
        title="Road closures"
        note="Roads within the radius are removed from your routes until the closure ends. Other organisations are not affected. Live closures are not available from a public source, so record the ones you hear about."
      >
        {closures.length === 0 ? (
          <p className="text-sm text-muted-foreground">No closures recorded.</p>
        ) : (
          <Card>
            <ul className="divide-y divide-border text-sm">
              {closures.map((x) => (
                <li key={x.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                  <span>
                    <span className="font-medium">{x.reason || "Closure"}</span>
                    <span className="ml-2 text-xs text-muted-foreground tabular-nums">
                      {x.lat.toFixed(4)}, {x.lng.toFixed(4)} · {x.radius_m} m
                      {x.until ? ` · until ${new Date(x.until).toLocaleString()}` : ""}
                    </span>
                  </span>
                  {canEdit && (
                    <button
                      className="text-xs text-destructive"
                      onClick={() => removeClosure.mutate(x.id)}
                    >
                      Remove
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        )}
        {canEdit && (
          <form
            className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-5"
            onSubmit={(e) => {
              e.preventDefault();
              addClosure.mutate();
            }}
          >
            <Input
              aria-label="Closure latitude"
              placeholder="Latitude"
              type="number"
              step="any"
              required
              value={c.lat}
              onChange={(e) => setC({ ...c, lat: e.target.value })}
            />
            <Input
              aria-label="Closure longitude"
              placeholder="Longitude"
              type="number"
              step="any"
              required
              value={c.lng}
              onChange={(e) => setC({ ...c, lng: e.target.value })}
            />
            <Input
              aria-label="Closure radius in metres"
              placeholder="Radius (m)"
              type="number"
              min={1}
              max={5000}
              required
              value={c.radius_m}
              onChange={(e) => setC({ ...c, radius_m: e.target.value })}
            />
            <Input
              aria-label="Closure reason"
              placeholder="Reason"
              value={c.reason}
              onChange={(e) => setC({ ...c, reason: e.target.value })}
            />
            <Input
              aria-label="Hours until reopening"
              placeholder="Hours (optional)"
              type="number"
              min={1}
              value={c.until_hours}
              onChange={(e) => setC({ ...c, until_hours: e.target.value })}
            />
            <Btn type="submit" disabled={addClosure.isPending} className="col-span-2 md:col-span-1">
              Add closure
            </Btn>
          </form>
        )}
        <ErrorNote error={addClosure.error ?? removeClosure.error} />
      </Section>
    </>
  );
}
