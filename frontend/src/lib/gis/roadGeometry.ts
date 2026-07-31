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
 * Fallback: a cubic (S-curve) bezier with a couple of terrain-flavoured
 * wobbles. Rougher / steeper roads bend more, exactly as they would in hill
 * country. This is used whenever OSRM can't snap an edge onto a real road
 * (unreachable pair, rate limit, timeout) — it must never be so gentle that
 * it reads as a straight line, or the map would regress to exactly the
 * "invented" straight edges this module exists to avoid.
 */
export function syntheticRoad(a: LatLng, b: LatLng, edge: RoadEdge | undefined, key: string): LatLng[] {
  // Minimum bend is deliberately high enough that even a "paved, flat, short"
  // fallback edge still reads as a winding road rather than a straight line.
  const bendBase =
    edge?.road_type === "rough" ? 0.3 : edge?.road_type === "gravel" ? 0.22 : 0.16;
  const bend = bendBase + (edge ? Math.min(edge.slope_pct, 20) / 160 : 0.05);
  const sign = hashUnit(key) >= 0 ? 1 : -1;

  const dLat = b[0] - a[0];
  const dLng = b[1] - a[1];

  // Two control points offset in opposite directions (S-curve) instead of a
  // single bow — a single gentle bow can still look near-straight at a
  // glance; an S-curve never does.
  const pLat = -dLng * bend * sign;
  const pLng = dLat * bend * sign;
  const c1Lat = a[0] + dLat * 0.33 + pLat * 0.9;
  const c1Lng = a[1] + dLng * 0.33 + pLng * 0.9;
  const c2Lat = a[0] + dLat * 0.66 - pLat * 0.65;
  const c2Lng = a[1] + dLng * 0.66 - pLng * 0.65;

  const pts: LatLng[] = [];
  const steps = 28;
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const mt = 1 - t;
    // cubic bezier: a -> c1 -> c2 -> b
    let lat =
      mt * mt * mt * a[0] + 3 * mt * mt * t * c1Lat + 3 * mt * t * t * c2Lat + t * t * t * b[0];
    let lng =
      mt * mt * mt * a[1] + 3 * mt * mt * t * c1Lng + 3 * mt * t * t * c2Lng + t * t * t * b[1];
    // deterministic jitter so the line reads as a road, not a smooth spline
    const wobble = Math.sin(t * Math.PI * 5 + hashUnit(key + i) * 2.5) * 0.0016 * (bend / 0.15);
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

/** fetchOsrm, retried a couple of times before we accept it's really down. */
async function fetchOsrmWithRetry(a: LatLng, b: LatLng, attempts = 3): Promise<LatLng[] | null> {
  for (let i = 0; i < attempts; i++) {
    const result = await fetchOsrm(a, b);
    if (result) return result;
    if (i < attempts - 1) {
      // Brief backoff — most OSRM misses here are transient (rate limit /
      // timeout under concurrency), not a genuinely unroutable pair.
      await new Promise((resolve) => setTimeout(resolve, 350 * (i + 1)));
    }
  }
  return null;
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
    const snapped = await fetchOsrmWithRetry(a, b);
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