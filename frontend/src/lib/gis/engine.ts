/**
 * TypeScript port of the Python routing engine so the demo runs with no
 * separate process. Formula-for-formula equivalent to:
 *   simulate.py, edge_weight.py, routing.py, feasibility.py,
 *   source_selector.py, service.py, gemma_reasoner.py (MockGemmaReasoner).
 *
 * When GIS_API_BASE is configured the real FastAPI backend is used instead
 * (see gis.functions.ts); this module is the offline fallback.
 */
import type {
  CandidatePlan,
  DeliveryRequest,
  NetworkResponse,
  OptimizeResponse,
  PlanSummary,
  RoadEdge,
  RoadType,
  RouteLeg,
  StorageNode,
} from "./types";

/* ---------------------------------------------------------------- rng ---- */

/** Deterministic PRNG so the simulated network is stable across requests. */
function makeRng(seed: number) {
  let s = seed >>> 0;
  const next = () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return {
    random: next,
    uniform: (a: number, b: number) => a + next() * (b - a),
    randint: (a: number, b: number) => a + Math.floor(next() * (b - a + 1)),
    choice: <T>(arr: readonly T[]): T => arr[Math.floor(next() * arr.length)]!,
    shuffle: <T>(arr: T[]) => {
      for (let i = arr.length - 1; i > 0; i--) {
        const j = Math.floor(next() * (i + 1));
        [arr[i], arr[j]] = [arr[j]!, arr[i]!];
      }
      return arr;
    },
  };
}

const round = (v: number, d: number) => {
  const f = 10 ** d;
  return Math.round(v * f) / f;
};

/* ----------------------------------------------------------- simulate ---- */

const ROAD_TYPES: RoadType[] = ["paved", "gravel", "rough"];

export function simulateNetwork(
  numNodes = 12,
  seed = 7,
  extraEdgeProb = 0.22,
): { nodes: Record<string, StorageNode>; edges: RoadEdge[] } {
  const rng = makeRng(seed);
  const nodes: Record<string, StorageNode> = {};

  for (let i = 0; i < numNodes; i++) {
    const id = `W${i}`;
    nodes[id] = {
      node_id: id,
      x: round(rng.uniform(0, 100), 1),
      y: round(rng.uniform(0, 100), 1),
      units_in_stock: rng.randint(0, 900),
      vehicles_available: rng.randint(0, 4),
      vehicle_capacity: 200,
    };
  }

  const nodeIds = rng.shuffle(Object.keys(nodes));
  const edges: RoadEdge[] = [];
  const seen = new Set<string>();

  const randomEdge = (a: string, b: string): RoadEdge => ({
    node_a: a,
    node_b: b,
    length_km: round(rng.uniform(3, 40), 1),
    slope_pct: round(rng.uniform(0, 25), 1),
    road_type: rng.choice(ROAD_TYPES),
    max_speed_kmph: rng.choice([20, 30, 40, 60]),
    hazard_score: round(rng.uniform(0, 1), 2),
    intersection: rng.random() < 0.15,
    weight: null,
    time_hours: null,
    fuel_estimate: null,
  });

  const addEdge = (a: string, b: string) => {
    const key = [a, b].sort().join("|");
    if (a === b || seen.has(key)) return;
    seen.add(key);
    edges.push(randomEdge(a, b));
  };

  // Spanning structure first — guarantees the network is connected.
  for (let i = 1; i < nodeIds.length; i++) {
    addEdge(nodeIds[i]!, nodeIds[rng.randint(0, i - 1)]!);
  }
  // Extra edges so alternate routes exist.
  for (let i = 0; i < nodeIds.length; i++) {
    for (let j = i + 1; j < nodeIds.length; j++) {
      if (rng.random() < extraEdgeProb) addEdge(nodeIds[i]!, nodeIds[j]!);
    }
  }

  return { nodes, edges };
}

/* -------------------------------------------------------- edge weight ---- */

const ROAD_TYPE_COST_PENALTY: Record<RoadType, number> = { paved: 1.0, gravel: 1.3, rough: 1.8 };
const ROAD_TYPE_SPEED_FACTOR: Record<RoadType, number> = { paved: 1.0, gravel: 0.75, rough: 0.5 };
const ROAD_TYPE_FUEL_FACTOR: Record<RoadType, number> = { paved: 1.0, gravel: 1.2, rough: 1.5 };

export function computeEdgeWeight(e: RoadEdge): number {
  const base = e.length_km * 1.0;
  const slopePenalty = (e.slope_pct * 0.05 * e.length_km) / 10;
  const roadPenalty = (ROAD_TYPE_COST_PENALTY[e.road_type] - 1.0) * e.length_km * 1.0;
  const hazardPenalty = e.hazard_score * 8.0;
  return round(base + slopePenalty + roadPenalty + hazardPenalty, 3);
}

export function computeEdgeTimeHours(e: RoadEdge): number {
  let speed = e.max_speed_kmph * ROAD_TYPE_SPEED_FACTOR[e.road_type];
  speed *= Math.max(0.4, 1 - e.slope_pct / 40);
  speed = Math.max(speed, 5);
  return round(e.length_km / speed, 3);
}

export function computeEdgeFuel(e: RoadEdge): number {
  const slopeFactor = 1 + e.slope_pct / 20;
  return round(e.length_km * slopeFactor * ROAD_TYPE_FUEL_FACTOR[e.road_type], 2);
}

/* ------------------------------------------------------------- graph ---- */

interface EdgeAttrs {
  weight: number;
  time: number;
  fuel: number;
  length: number;
}

export interface Graph {
  nodes: string[];
  adj: Map<string, Map<string, EdgeAttrs>>;
}

export function buildGraph(nodes: Record<string, StorageNode>, edges: RoadEdge[]): Graph {
  const adj = new Map<string, Map<string, EdgeAttrs>>();
  for (const id of Object.keys(nodes)) adj.set(id, new Map());
  for (const e of edges) {
    e.weight = computeEdgeWeight(e);
    e.time_hours = computeEdgeTimeHours(e);
    e.fuel_estimate = computeEdgeFuel(e);
    const attrs: EdgeAttrs = {
      weight: e.weight,
      time: e.time_hours,
      fuel: e.fuel_estimate,
      length: e.length_km,
    };
    if (!adj.has(e.node_a)) adj.set(e.node_a, new Map());
    if (!adj.has(e.node_b)) adj.set(e.node_b, new Map());
    adj.get(e.node_a)!.set(e.node_b, attrs);
    adj.get(e.node_b)!.set(e.node_a, attrs);
  }
  return { nodes: Object.keys(nodes), adj };
}

type Blocked = { edges: Set<string>; nodes: Set<string> };
const edgeKey = (a: string, b: string) => [a, b].sort().join("|");

/** Dijkstra on `weight`, honouring temporarily removed nodes/edges (Yen). */
function dijkstra(G: Graph, source: string, target: string, blocked?: Blocked): string[] | null {
  if (!G.adj.has(source) || !G.adj.has(target)) return null;
  if (blocked?.nodes.has(source) || blocked?.nodes.has(target)) return null;

  const dist = new Map<string, number>([[source, 0]]);
  const prev = new Map<string, string>();
  const visited = new Set<string>();
  const queue = new Set<string>([source]);

  while (queue.size) {
    let u: string | null = null;
    let best = Infinity;
    for (const n of queue) {
      const d = dist.get(n) ?? Infinity;
      if (d < best) {
        best = d;
        u = n;
      }
    }
    if (u === null) break;
    queue.delete(u);
    visited.add(u);
    if (u === target) break;

    for (const [v, attrs] of G.adj.get(u) ?? []) {
      if (visited.has(v) || blocked?.nodes.has(v)) continue;
      if (blocked?.edges.has(edgeKey(u, v))) continue;
      const alt = best + attrs.weight;
      if (alt < (dist.get(v) ?? Infinity)) {
        dist.set(v, alt);
        prev.set(v, u);
        queue.add(v);
      }
    }
  }

  if (!dist.has(target)) return null;
  const path = [target];
  let cur = target;
  while (cur !== source) {
    const p = prev.get(cur);
    if (p === undefined) return null;
    path.unshift(p);
    cur = p;
  }
  return path;
}

export function pathMetrics(G: Graph, path: string[]) {
  let distance = 0;
  let time = 0;
  let cost = 0;
  let fuel = 0;
  for (let i = 0; i < path.length - 1; i++) {
    const a = G.adj.get(path[i]!)?.get(path[i + 1]!);
    if (!a) continue;
    distance += a.length;
    time += a.time;
    cost += a.weight;
    fuel += a.fuel;
  }
  return { distance, time, cost, fuel };
}

/** Yen's K-shortest loopless paths, ranked by edge-weight cost. */
export function kShortestPaths(G: Graph, source: string, target: string, k = 3): string[][] {
  const first = dijkstra(G, source, target);
  if (!first) return [];
  const A: string[][] = [first];
  const B: { path: string[]; cost: number }[] = [];

  while (A.length < k) {
    const prevPath = A[A.length - 1]!;
    for (let i = 0; i < prevPath.length - 1; i++) {
      const spurNode = prevPath[i]!;
      const rootPath = prevPath.slice(0, i + 1);
      const blocked: Blocked = { edges: new Set(), nodes: new Set() };

      for (const p of A) {
        if (
          p.length > i &&
          rootPath.every((n, idx) => p[idx] === n)
        ) {
          blocked.edges.add(edgeKey(p[i]!, p[i + 1] ?? p[i]!));
        }
      }
      for (const n of rootPath.slice(0, -1)) blocked.nodes.add(n!);

      const spurPath = dijkstra(G, spurNode, target, blocked);
      if (!spurPath) continue;
      const total: string[] = [...rootPath.slice(0, -1), ...spurPath];
      const key = total.join(">");
      if (A.some((p) => p.join(">") === key) || B.some((c) => c.path.join(">") === key)) continue;
      B.push({ path: total, cost: pathMetrics(G, total).cost });
    }
    if (!B.length) break;
    B.sort((a, b) => a.cost - b.cost);
    A.push(B.shift()!.path);
  }
  return A;
}

export interface RouteResult {
  path: string[];
  distance_km: number;
  time_hours: number;
  edge_cost: number;
  fuel: number;
}

export function bestRoute(G: Graph, source: string, target: string, k = 3): RouteResult | null {
  if (source === target) {
    return { path: [source], distance_km: 0, time_hours: 0, edge_cost: 0, fuel: 0 };
  }
  const candidates = kShortestPaths(G, source, target, k);
  if (!candidates.length) return null;
  let best = candidates[0]!;
  let bestCost = pathMetrics(G, best).cost;
  for (const p of candidates.slice(1)) {
    const c = pathMetrics(G, p).cost;
    if (c < bestCost) {
      best = p;
      bestCost = c;
    }
  }
  const m = pathMetrics(G, best);
  return {
    path: best,
    distance_km: m.distance,
    time_hours: m.time,
    edge_cost: m.cost,
    fuel: m.fuel,
  };
}

/** Exact Held-Karp open-path TSP over shortlisted warehouses. */
export function heldKarpTsp(
  G: Graph,
  start: string,
  stopsIn: string[],
): (RouteResult & { visit_order: string[] }) | null {
  const stops = stopsIn.filter((s) => s !== start);
  if (!stops.length) {
    return {
      visit_order: [start],
      path: [start],
      distance_km: 0,
      time_hours: 0,
      edge_cost: 0,
      fuel: 0,
    };
  }
  const points: string[] = [start, ...stops];
  const n = points.length;
  const pairPath = new Map<string, string[]>();
  const pairCost = new Map<string, number>();

  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      if (i === j) continue;
      const p = dijkstra(G, points[i]!, points[j]!);
      if (!p) return null;
      pairPath.set(`${i},${j}`, p);
      pairCost.set(`${i},${j}`, pathMetrics(G, p).cost);
    }
  }

  const stopIdx = Array.from({ length: n - 1 }, (_, i) => i + 1);
  // dp keyed by "bitmask|last" -> { cost, prev }
  const dp = new Map<string, { cost: number; prev: number }>();
  for (const j of stopIdx) dp.set(`${1 << j}|${j}`, { cost: pairCost.get(`0,${j}`)!, prev: 0 });

  const subsets: number[][] = [];
  const gen = (start_: number, acc: number[]) => {
    if (acc.length) subsets.push([...acc]);
    for (let i = start_; i < stopIdx.length; i++) gen(i + 1, [...acc, stopIdx[i]!]);
  };
  gen(0, []);
  subsets.sort((a, b) => a.length - b.length);

  for (const subset of subsets) {
    if (subset.length < 2) continue;
    const mask = subset.reduce((m, j) => m | (1 << j), 0);
    for (const j of subset) {
      const prevMask = mask & ~(1 << j);
      let best: { cost: number; prev: number } | null = null;
      for (const kk of subset) {
        if (kk === j) continue;
        const e = dp.get(`${prevMask}|${kk}`);
        if (!e) continue;
        const cand = e.cost + pairCost.get(`${kk},${j}`)!;
        if (!best || cand < best.cost) best = { cost: cand, prev: kk };
      }
      if (best) dp.set(`${mask}|${j}`, best);
    }
  }

  const fullMask = stopIdx.reduce((m, j) => m | (1 << j), 0);
  let endCost = Infinity;
  let endNode = -1;
  for (const j of stopIdx) {
    const e = dp.get(`${fullMask}|${j}`);
    if (e && e.cost < endCost) {
      endCost = e.cost;
      endNode = j;
    }
  }
  if (endNode === -1) return null;

  const order: number[] = [endNode];
  let mask = fullMask;
  let last = endNode;
  while (order.length < stopIdx.length) {
    const e = dp.get(`${mask}|${last}`)!;
    order.push(e.prev);
    mask = mask & ~(1 << last);
    last = e.prev;
  }
  order.reverse();

  const visitOrder: string[] = [points[0]!, ...order.filter((i) => i !== 0).map((i) => points[i]!)];
  const fullPath: string[] = [visitOrder[0]!];
  let distance = 0;
  let time = 0;
  let fuel = 0;
  for (let i = 0; i < visitOrder.length - 1; i++) {
    const ia = points.indexOf(visitOrder[i]!);
    const ib = points.indexOf(visitOrder[i + 1]!);
    const seg = pairPath.get(`${ia},${ib}`)!;
    fullPath.push(...seg.slice(1));
    const m = pathMetrics(G, seg);
    distance += m.distance;
    time += m.time;
    fuel += m.fuel;
  }

  return {
    visit_order: visitOrder,
    path: fullPath,
    distance_km: distance,
    time_hours: time,
    edge_cost: endCost,
    fuel,
  };
}

/* ------------------------------------------------------- feasibility ---- */

const maxDeliverable = (n: StorageNode) => n.vehicles_available * n.vehicle_capacity;
const canFulfillAlone = (n: StorageNode, q: number) =>
  n.units_in_stock >= q && maxDeliverable(n) >= q;
const vehiclesNeeded = (q: number, cap = 200) => Math.ceil(q / cap);

/* ---------------------------------------------------- source selector ---- */

export function findSingleSourcePlans(
  G: Graph,
  nodes: Record<string, StorageNode>,
  req: DeliveryRequest,
  k = 3,
): CandidatePlan[] {
  const plans: CandidatePlan[] = [];
  for (const [id, node] of Object.entries(nodes)) {
    if (id === req.destination || !canFulfillAlone(node, req.quantity)) continue;
    const route = bestRoute(G, id, req.destination, k);
    if (!route) continue;
    const nVehicles = vehiclesNeeded(req.quantity, node.vehicle_capacity);
    const leg: RouteLeg = {
      source: id,
      path: route.path,
      time_hours: route.time_hours,
      edge_cost: route.edge_cost,
      units_carried: req.quantity,
      vehicles_used: nVehicles,
      fuel_estimate: route.fuel,
      distance_km: route.distance_km,
    };
    plans.push({
      legs: [leg],
      plan_type: "single_source",
      total_units: req.quantity,
      total_time_hours: route.time_hours,
      total_edge_cost: route.edge_cost,
      fuel_estimate: route.fuel,
      vehicles_used: nVehicles,
      sources_used: [id],
    });
  }
  return plans;
}

function* combinations<T>(arr: T[], r: number): Generator<T[]> {
  if (r > arr.length) return;
  const idx = Array.from({ length: r }, (_, i) => i);
  while (true) {
    yield idx.map((i) => arr[i]!);
    let i = r - 1;
    while (i >= 0 && idx[i]! === i + arr.length - r) i--;
    if (i < 0) return;
    idx[i] = idx[i]! + 1;
    for (let j = i + 1; j < r; j++) idx[j] = idx[j - 1]! + 1;
  }
}

export function findMultiSourceParallelPlans(
  G: Graph,
  nodes: Record<string, StorageNode>,
  req: DeliveryRequest,
  k = 3,
  maxSources = 4,
): CandidatePlan[] {
  const candidates = Object.entries(nodes).filter(
    ([id, n]) => id !== req.destination && n.units_in_stock > 0 && n.vehicles_available > 0,
  );
  const plans: CandidatePlan[] = [];

  for (let r = 2; r <= Math.min(maxSources, candidates.length); r++) {
    for (const combo of combinations(candidates, r)) {
      const totalStock = combo.reduce((s, [, n]) => s + n.units_in_stock, 0);
      const totalCapacity = combo.reduce((s, [, n]) => s + maxDeliverable(n), 0);
      if (totalStock < req.quantity || totalCapacity < req.quantity) continue;

      const legs: RouteLeg[] = [];
      let remaining = req.quantity;
      let ok = true;

      for (const [id, node] of combo) {
        if (remaining <= 0) break;
        const take = Math.min(node.units_in_stock, maxDeliverable(node), remaining);
        if (take <= 0) continue;
        const route = bestRoute(G, id, req.destination, k);
        if (!route) {
          ok = false;
          break;
        }
        legs.push({
          source: id,
          path: route.path,
          time_hours: route.time_hours,
          edge_cost: route.edge_cost,
          units_carried: take,
          vehicles_used: vehiclesNeeded(take, node.vehicle_capacity),
          fuel_estimate: route.fuel,
          distance_km: route.distance_km,
        });
        remaining -= take;
      }

      if (!ok || remaining > 0 || !legs.length) continue;

      plans.push({
        legs,
        plan_type: "multi_source_parallel",
        total_units: req.quantity,
        total_time_hours: Math.max(...legs.map((l) => l.time_hours)),
        total_edge_cost: legs.reduce((s, l) => s + l.edge_cost, 0),
        fuel_estimate: legs.reduce((s, l) => s + l.fuel_estimate, 0),
        vehicles_used: legs.reduce((s, l) => s + l.vehicles_used, 0),
        sources_used: legs.map((l) => l.source),
      });
    }
  }
  return plans;
}

export function findSingleVehicleMultistopPlan(
  G: Graph,
  nodes: Record<string, StorageNode>,
  req: DeliveryRequest,
  vehicleHome: string,
  vehicleCapacity = 200,
  maxPickupStops = 4,
  k = 3,
): CandidatePlan | null {
  let remaining = req.quantity;
  const stockPool = new Map<string, number>(
    Object.entries(nodes)
      .filter(([id]) => id !== req.destination)
      .map(([id, n]) => [id, n.units_in_stock]),
  );
  const trips: {
    pickups: [string, number][];
    path: string[];
    time_hours: number;
    edge_cost: number;
    fuel: number;
    units_carried: number;
  }[] = [];
  let totalTime = 0;
  let totalCost = 0;
  let totalFuel = 0;
  let firstTrip = true;

  const distFromHome = (id: string) => {
    const p = dijkstra(G, vehicleHome, id);
    return p ? pathMetrics(G, p).cost : Infinity;
  };

  while (remaining > 0) {
    const takeTarget = Math.min(remaining, vehicleCapacity);
    const ranked = [...stockPool.entries()]
      .filter(([, s]) => s > 0)
      .sort((a, b) => distFromHome(a[0]) - distFromHome(b[0]));

    const picked: [string, number][] = [];
    let covered = 0;
    for (const [id, stock] of ranked) {
      if (covered >= takeTarget || picked.length >= maxPickupStops) break;
      const take = Math.min(stock, takeTarget - covered);
      if (take <= 0) continue;
      picked.push([id, take]);
      covered += take;
    }
    if (covered < takeTarget) return null;

    const pickupLeg = heldKarpTsp(
      G,
      vehicleHome,
      picked.map(([id]) => id),
    );
    if (!pickupLeg) return null;
    const lastStop = pickupLeg.visit_order[pickupLeg.visit_order.length - 1]!;
    const finalRoute = bestRoute(G, lastStop, req.destination, k);
    if (!finalRoute) return null;

    let tripTime = pickupLeg.time_hours + finalRoute.time_hours;
    let tripCost = pickupLeg.edge_cost + finalRoute.edge_cost;
    let tripFuel = pickupLeg.fuel + finalRoute.fuel;

    if (!firstTrip) {
      const back = bestRoute(G, req.destination, vehicleHome, 1);
      if (back) {
        tripTime += back.time_hours;
        tripCost += back.edge_cost;
        tripFuel += back.fuel;
      }
    }

    trips.push({
      pickups: picked,
      path: [...pickupLeg.visit_order, ...finalRoute.path.slice(1)],
      time_hours: tripTime,
      edge_cost: tripCost,
      fuel: tripFuel,
      units_carried: covered,
    });
    totalTime += tripTime;
    totalCost += tripCost;
    totalFuel += tripFuel;

    for (const [id, take] of picked) stockPool.set(id, (stockPool.get(id) ?? 0) - take);
    remaining -= covered;
    firstTrip = false;
  }

  const legs: RouteLeg[] = trips.map((t) => ({
    source: t.pickups.map(([id]) => id).join("+"),
    path: t.path,
    time_hours: t.time_hours,
    edge_cost: t.edge_cost,
    units_carried: t.units_carried,
    vehicles_used: 1,
    fuel_estimate: t.fuel,
    distance_km: 0,
  }));
  const sources = [...new Set(trips.flatMap((t) => t.pickups.map(([id]) => id)))].sort();

  return {
    legs,
    plan_type: "single_vehicle_multistop",
    total_units: req.quantity,
    total_time_hours: totalTime,
    total_edge_cost: totalCost,
    fuel_estimate: totalFuel,
    vehicles_used: 1,
    sources_used: sources,
  };
}

/* -------------------------------------------------- reasoning + service -- */

export function planSummary(plan: CandidatePlan): PlanSummary {
  return {
    plan_type: plan.plan_type,
    sources_used: plan.sources_used,
    vehicles_used: plan.vehicles_used,
    total_units: plan.total_units,
    total_time_hours: round(plan.total_time_hours, 2),
    total_edge_cost: round(plan.total_edge_cost, 2),
    fuel_estimate: round(plan.fuel_estimate, 2),
    legs: plan.legs,
  };
}

/**
 * Same selection policy as MockGemmaReasoner: among plans meeting the
 * deadline, prefer fewest sources, tie-break on lower total edge cost.
 * Used when no Gemma/LLM layer is available.
 */
export function selectAndExplain(req: DeliveryRequest, plans: CandidatePlan[]): OptimizeResponse {
  const feasible = plans.filter((p) => p.total_time_hours <= req.deadline_hours);

  if (!feasible.length) {
    const reason = plans.length
      ? `The fastest possible plan found still takes ${Math.min(
          ...plans.map((p) => p.total_time_hours),
        ).toFixed(1)}h, which exceeds the ${req.deadline_hours}h deadline.`
      : "No combination of warehouses in the network can supply the requested quantity at all.";
    return {
      status: "infeasible",
      explanation: `Cannot deliver ${req.quantity} units to ${req.destination} within ${req.deadline_hours} hours. ${reason}`,
      chosen_plan: null,
      rejected_plans: plans.map(planSummary),
    };
  }

  const chosen = feasible.reduce((a, b) => {
    if (a.sources_used.length !== b.sources_used.length)
      return a.sources_used.length < b.sources_used.length ? a : b;
    return a.total_edge_cost <= b.total_edge_cost ? a : b;
  });
  const rejected = feasible.filter((p) => p !== chosen);

  const lines = [
    `Recommended: ${chosen.plan_type.replace(/_/g, " ")} using ${chosen.sources_used.length} source(s) (${chosen.sources_used.join(", ")}) and ${chosen.vehicles_used} vehicle(s).`,
    `Arrives in ${chosen.total_time_hours.toFixed(1)}h, within the ${req.deadline_hours}h deadline.`,
  ];
  if (rejected.length) {
    const fewer = rejected.reduce((a, b) =>
      a.sources_used.length <= b.sources_used.length ? a : b,
    );
    lines.push(
      `Other feasible plans needed ${fewer.sources_used.length}+ source(s) or more vehicles without a meaningful time or cost advantage, so this one was preferred.`,
    );
  }

  return {
    status: "feasible",
    explanation: lines.join(" "),
    chosen_plan: planSummary(chosen),
    rejected_plans: rejected.map(planSummary),
  };
}

/** service.py :: process_request — fulfillment priority order preserved. */
export function processRequest(
  nodes: Record<string, StorageNode>,
  G: Graph,
  req: DeliveryRequest,
): OptimizeResponse {
  let plans = findSingleSourcePlans(G, nodes, req);
  if (!plans.length) plans = findMultiSourceParallelPlans(G, nodes, req);
  if (!plans.length) {
    const home = Object.entries(nodes).find(([, n]) => n.vehicles_available > 0)?.[0];
    if (home) {
      const multistop = findSingleVehicleMultistopPlan(G, nodes, req, home);
      if (multistop) plans = [multistop];
    }
  }
  return { ...selectAndExplain(req, plans), reasoner_used: "local_policy" };
}

/* -------------------------------------------------------- singletons ---- */

let cached: { network: NetworkResponse; nodes: Record<string, StorageNode>; graph: Graph } | null =
  null;

export function getLocalNetwork() {
  if (!cached) {
    const { nodes, edges } = simulateNetwork(12, 11);
    const graph = buildGraph(nodes, edges);
    cached = { nodes, graph, network: { nodes: Object.values(nodes), edges } };
  }
  return cached;
}
