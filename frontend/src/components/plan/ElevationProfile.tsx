import { useMemo } from "react";
import { haversineKm } from "@/lib/format";
import type { Plan } from "@/lib/types";

/** Elevation along the chosen route, from the SRTM heights stored on every road vertex. */
export function ElevationProfile({ plan }: { plan: Plan }) {
  const { pts, minZ, maxZ, km } = useMemo(() => {
    const out: [number, number][] = [];
    let d = 0;
    let prev: [number, number] | null = null;
    for (const leg of plan.legs) {
      for (const [lat, lng, z] of leg.geometry) {
        if (prev) d += haversineKm(prev, [lat, lng]);
        prev = [lat, lng];
        out.push([d, z]);
      }
      prev = null; // legs are separate trips
    }
    const zs = out.map((p) => p[1]);
    return { pts: out, minZ: Math.min(...zs), maxZ: Math.max(...zs), km: out.at(-1)?.[0] ?? 0 };
  }, [plan]);

  if (pts.length < 2 || km <= 0) return null;
  const W = 320;
  const H = 72;
  const pad = 4;
  const span = Math.max(maxZ - minZ, 10);
  const x = (d: number) => pad + (d / km) * (W - 2 * pad);
  const y = (z: number) => H - pad - ((z - minZ) / span) * (H - 2 * pad);
  // Downsample to keep the path small on long routes.
  const step = Math.max(1, Math.floor(pts.length / 160));
  const sample = pts.filter((_, i) => i % step === 0 || i === pts.length - 1);
  const line = sample
    .map(([d, z], i) => `${i ? "L" : "M"}${x(d).toFixed(1)},${y(z).toFixed(1)}`)
    .join(" ");
  const ascent = plan.legs.reduce((s, l) => s + l.ascent_m, 0);
  const descent = plan.legs.reduce((s, l) => s + l.descent_m, 0);

  return (
    <figure className="space-y-1.5">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-[72px] w-full"
        role="img"
        aria-label="Elevation profile of the route"
      >
        <path
          d={`${line} L${x(sample.at(-1)![0])},${H - pad} L${x(0)},${H - pad} Z`}
          className="fill-primary/10"
        />
        <path
          d={line}
          className="stroke-primary"
          fill="none"
          strokeWidth={1.5}
          strokeLinejoin="round"
        />
      </svg>
      <figcaption className="flex justify-between text-[11px] text-muted-foreground tabular-nums">
        <span>
          {Math.round(minZ)}–{Math.round(maxZ)} m
        </span>
        <span>
          ↑ {Math.round(ascent)} m · ↓ {Math.round(descent)} m
        </span>
      </figcaption>
    </figure>
  );
}
