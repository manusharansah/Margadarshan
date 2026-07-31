/**
 * Mirrors gis_backend/models.py exactly (workflow doc Section 3).
 * These are also the wire shapes returned by the FastAPI backend's
 * GET /network and POST /optimize-route, so the same types serve both
 * the local engine and the live backend.
 */

export interface StorageNode {
  node_id: string;
  x: number;
  y: number;
  units_in_stock: number;
  vehicles_available: number;
  vehicle_capacity: number;
}

export type RoadType = "paved" | "gravel" | "rough";

export interface RoadEdge {
  node_a: string;
  node_b: string;
  length_km: number;
  slope_pct: number;
  road_type: RoadType;
  max_speed_kmph: number;
  hazard_score: number;
  intersection: boolean;
  weight: number | null;
  time_hours: number | null;
  fuel_estimate: number | null;
}

export interface NetworkResponse {
  nodes: StorageNode[];
  edges: RoadEdge[];
}

export interface RouteLeg {
  source: string;
  path: string[];
  time_hours: number;
  edge_cost: number;
  units_carried: number;
  vehicles_used: number;
  fuel_estimate: number;
  distance_km: number;
}

export type PlanType =
  | "single_source"
  | "multi_source_parallel"
  | "single_vehicle_multistop";

export interface CandidatePlan {
  legs: RouteLeg[];
  plan_type: PlanType;
  total_units: number;
  total_time_hours: number;
  total_edge_cost: number;
  fuel_estimate: number;
  vehicles_used: number;
  sources_used: string[];
}

/** The `_plan_summary()` shape the API serializes. */
export interface PlanSummary {
  plan_type: PlanType;
  sources_used: string[];
  vehicles_used: number;
  total_units: number;
  total_time_hours: number;
  total_edge_cost: number;
  fuel_estimate: number;
  /** Not in the Python summary; added locally so the map can draw the plan. */
  legs?: RouteLeg[];
}

export interface DeliveryRequest {
  destination: string;
  quantity: number;
  deadline_hours: number;
}

export interface OptimizeResponse {
  status: "feasible" | "infeasible" | "reasoning_unavailable";
  explanation: string;
  chosen_plan: PlanSummary | null;
  rejected_plans?: PlanSummary[];
  candidate_plans?: PlanSummary[];
  reasoner_used?: string;
  gemma_error?: string;
}
