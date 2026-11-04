import { createFileRoute } from "@tanstack/react-router";
import { useMeta } from "@/lib/api";

export const Route = createFileRoute("/data")({ component: DataPage });

function DataPage() {
  const { data: m, error } = useMeta();
  if (error) return <p className="p-8 text-sm text-destructive">{error.message}</p>;
  if (!m) return <p className="p-8 text-sm text-muted-foreground">Loading…</p>;
  const n = m.network;
  const fetched = m.weather.fetched_at
    ? new Date(m.weather.fetched_at * 1000).toLocaleString()
    : null;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-8 p-4 md:p-8">
        <div>
          <h1 className="text-xl font-semibold">Data & methods</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Where every number comes from, and what is assumed.
          </p>
        </div>

        <section>
          <h2 className="mb-2 text-sm font-semibold">Data sources</h2>
          <div className="divide-y divide-border rounded-xl border border-border bg-card text-sm">
            <Row k="Roads" v={n.sources.roads} />
            <Row k="Elevation / slope" v={n.sources.elevation} />
            <Row k="Past incidents" v={n.sources.incidents} />
            <Row
              k="Rainfall"
              v={
                m.weather.available
                  ? `${m.weather.source ?? "Open-Meteo"} · updated ${fetched}`
                  : `Unavailable${m.weather.error ? ` — ${m.weather.error.slice(0, 120)}` : ""}`
              }
            />
            <Row k="Warehouses" v={`${m.warehouses} registered by the operator`} />
            <Row
              k="Explanations"
              v={
                m.reasoner === "gemma"
                  ? "Gemma (Google AI Studio)"
                  : "Rule-based summary (Gemma not configured)"
              }
            />
          </div>
        </section>

        <section>
          <h2 className="mb-2 text-sm font-semibold">Network</h2>
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border bg-border text-sm sm:grid-cols-4">
            <Stat k="Road segments" v={n.counts.edges.toLocaleString()} />
            <Stat k="Junctions" v={n.counts.nodes.toLocaleString()} />
            <Stat k="Length" v={`${n.network_km.toLocaleString()} km`} />
            <Stat k="Surface inferred" v={`${Math.round(n.surface_inferred_fraction * 100)}%`} />
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            Built {new Date(n.built_at).toLocaleString()}. "Surface inferred" is the share of roads
            where OpenStreetMap has no surface tag and the type was assumed from the road class.
          </p>
        </section>

        <section className="space-y-3 text-sm leading-relaxed">
          <h2 className="text-sm font-semibold">How a recommendation is made</h2>
          <ol className="list-decimal space-y-2 pl-5 text-muted-foreground">
            <li>The destination and each warehouse are snapped to the nearest mapped road.</li>
            <li>
              Every road piece gets a cost: its length, increased for gravel/rough surface and steep
              grade, plus a hazard term that grows with length and with recent rainfall.
            </li>
            <li>
              Candidate plans are generated in priority order — one warehouse, several in parallel,
              then one vehicle visiting several (exact travelling-salesman solution) — using
              shortest-path search.
            </li>
            <li>
              Plans that miss the deadline are discarded by code. Time, distance, fuel and units are
              arithmetic on the road data, never model output.
            </li>
            <li>
              The remaining plans are ranked (fewest warehouses, then fewest vehicles, then lowest
              cost). Gemma, when configured, may pick among them and writes the explanation.
            </li>
          </ol>
          <h2 className="pt-2 text-sm font-semibold">Limits to keep in mind</h2>
          <ul className="list-disc space-y-1.5 pl-5 text-muted-foreground">
            <li>
              The hazard index is a transparent screening score (terrain steepness, optional
              incident history, rainfall) — not a calibrated landslide probability.
            </li>
            <li>
              Grades come from ~30 m SRTM elevation data and can be off by several percent on short
              stretches.
            </li>
            <li>
              Travel speeds are planning defaults by road class and surface; one-way restrictions
              and live traffic or closures are not modelled.
            </li>
            <li>
              Fuel is an engineering estimate (base consumption plus climb energy) and should be
              calibrated against your fleet's logs.
            </li>
          </ul>
        </section>
      </div>
    </div>
  );
}

const Row = ({ k, v }: { k: string; v: string }) => (
  <div className="flex flex-col gap-0.5 px-4 py-3 sm:flex-row sm:gap-6">
    <dt className="w-36 shrink-0 text-muted-foreground">{k}</dt>
    <dd>{v}</dd>
  </div>
);
const Stat = ({ k, v }: { k: string; v: string }) => (
  <div className="bg-card px-4 py-3">
    <p className="text-[11px] text-muted-foreground">{k}</p>
    <p className="mt-0.5 font-semibold tabular-nums">{v}</p>
  </div>
);
