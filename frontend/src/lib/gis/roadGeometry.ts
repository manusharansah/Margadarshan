import { project } from "./geo";
import type { RoadEdge } from "./types";

/**
 * Purely visual layer.
 *
 * Every routing decision, weight, time and cost still comes from the engine /
 * FastAPI backend. This module only replaces the *drawn shape* of an edge:
 * instead of a straight line between two projected node positions, we snap the
 * pair onto the real OSM road network (OSRM) so the map shows roads that bend,
 * switch back and follow the terrain the way roads in Kavre actually do.
 *
 * If OSRM is unreachable we fall back to a deterministic curve derived from the
 * edge attributes, so the map never regresses to plain straight lines.
 */

export type LatLng = [number, number];

const OSRM = "https://router.project-osrm.org/route/v1/driving";

const cache = new Map<string, LatLng[]>();
const inflight = new Map<string, Promise<LatLng[]>>();

export function edgeKey(a: string, b: string) {
  return [a, b].sort().join("|");
}

/** Deterministic pseudo-random in [-1,1] from a string. */
function hashUnit(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) / 0xffffffff) * 2 - 1;
}

/**
 * Fallback: a quadratic bezier with a couple of terrain-flavoured wobbles.
 * Rougher / steeper roads bend more, exactly as they would in hill country.
 */
export function syntheticRoad(a: LatLng, b: LatLng, edge: RoadEdge | undefined, key: string): LatLng[] {
  const bendBase =
    edge?.road_type === "rough" ? 0.22 : edge?.road_type === "gravel" ? 0.14 : 0.07;
  const bend = bendBase + (edge ? Math.min(edge.slope_pct, 20) / 200 : 0);
  const sign = hashUnit(key) >= 0 ? 1 : -1;

  const dLat = b[0] - a[0];
  const dLng = b[1] - a[1];
  // perpendicular offset
  const pLat = -dLng * bend * sign;
  const pLng = dLat * bend * sign;
  const cLat = (a[0] + b[0]) / 2 + pLat;
  const cLng = (a[1] + b[1]) / 2 + pLng;

  const pts: LatLng[] = [];
  const steps = 24;
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const mt = 1 - t;
    let lat = mt * mt * a[0] + 2 * mt * t * cLat + t * t * b[0];
    let lng = mt * mt * a[1] + 2 * mt * t * cLng + t * t * b[1];
    // small deterministic jitter so the line reads as a road, not a spline
    const wobble = Math.sin(t * Math.PI * 3 + hashUnit(key + i) * 2) * 0.0009 * (bend / 0.15);
    lat += wobble * -dLng * 6;
    lng += wobble * dLat * 6;
    pts.push([lat, lng]);
  }
  pts[0] = a;
  pts[pts.length - 1] = b;
  return pts;
}

async function fetchOsrm(a: LatLng, b: LatLng): Promise<LatLng[] | null> {
  const url = `${OSRM}/${a[1]},${a[0]};${b[1]},${b[0]}?overview=full&geometries=geojson`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) return null;
    const json = (await res.json()) as {
      code?: string;
      routes?: { geometry?: { coordinates?: [number, number][] } }[];
    };
    const coords = json.code === "Ok" ? json.routes?.[0]?.geometry?.coordinates : null;
    if (!coords || coords.length < 2) return null;
    return coords.map(([lng, lat]) => [lat, lng] as LatLng);
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Real-road geometry for one edge, memoised per session. */
export function getRoadGeometry(
  key: string,
  a: LatLng,
  b: LatLng,
  edge?: RoadEdge,
): LatLng[] | Promise<LatLng[]> {
  const hit = cache.get(key);
  if (hit) return hit;
  const pending = inflight.get(key);
  if (pending) return pending;

  const p = (async () => {
    const snapped = await fetchOsrm(a, b);
    const geom = snapped ?? syntheticRoad(a, b, edge, key);
    cache.set(key, geom);
    inflight.delete(key);
    return geom;
  })();
  inflight.set(key, p);
  return p;
}

export function cachedGeometry(key: string): LatLng[] | undefined {
  return cache.get(key);
}

/**
 * Warm the cache for a whole edge list with limited concurrency, calling
 * `onProgress` whenever a batch lands so the map can redraw incrementally.
 */
export async function warmRoadGeometry(
  edges: RoadEdge[],
  pos: (nodeId: string) => LatLng | undefined,
  onProgress: () => void,
  concurrency = 6,
) {
  const queue = edges.filter((e) => !cache.has(edgeKey(e.node_a, e.node_b)));
  let i = 0;
  let landed = 0;

  async function worker() {
    while (i < queue.length) {
      const e = queue[i++]!;
      const a = pos(e.node_a);
      const b = pos(e.node_b);
      if (!a || !b) continue;
      await getRoadGeometry(edgeKey(e.node_a, e.node_b), a, b, e);
      landed++;
      if (landed % 4 === 0) onProgress();
    }
  }

  await Promise.all(Array.from({ length: concurrency }, worker));
  onProgress();
}

export { project };
