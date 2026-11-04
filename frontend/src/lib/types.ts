/** Wire types for the FastAPI backend (backend/gis_backend/api.py, service.py). */

export type LatLngElev = [lat: number, lng: number, elevation_m: number];

export type PlanType = "single_source" | "multi_source_parallel" | "single_vehicle_multistop";

export interface RouteLeg {
  source: string;
  time_hours: number;
  edge_cost: number;
  units_carried: number;
  vehicles_used: number;
  fuel_litres: number;
  distance_km: number;
  max_hazard: number;
  ascent_m: number;
  descent_m: number;
  geometry: LatLngElev[];
  pickups: string[];
  hotspots: {
    road: string;
    hazard: number;
    rain_index: number;
    grade_pct: number;
    surface: string;
  }[];
}

export interface Plan {
  legs: RouteLeg[];
  plan_type: PlanType;
  total_units: number;
  total_time_hours: number;
  total_edge_cost: number;
  fuel_litres: number;
  vehicles_used: number;
  distance_km: number;
  sources_used: string[];
  meets_deadline: boolean;
  reliability?: {
    eta_p10: number;
    eta_p50: number;
    eta_p90: number;
    p_late: number;
    draws: number;
  };
}

export interface OptimizeResponse {
  status: "feasible" | "infeasible";
  explanation: string;
  explanation_source: "gemma" | "deterministic";
  risk_note: string;
  risk_note_source?: "gemma" | "deterministic";
  result_id?: string;
  ai?: { status: "pending" | "done" | "partial"; error?: string };
  remedies: Remedy[];
  remedy_error?: string;
  destination: {
    name: string;
    lat: number;
    lng: number;
    snapped_lat: number;
    snapped_lng: number;
    snap_distance_m: number;
  };
  chosen_plan: Plan | null;
  chosen_matches_policy: boolean | null;
  alternatives: Plan[];
  alternatives_total: number;
  best_case_hours: number | null;
  candidates_evaluated: number;
  warehouses_considered: number;
  warnings: string[];
  weather: WeatherStatus | null;
  gemma_error?: string;
}

export interface Remedy {
  source: "computed" | "gemma";
  kind: string;
  quantity: number;
  deadline_hours: number;
  eta_hours: number;
  plan_type: PlanType;
  sources: string[];
  vehicles: number;
  rationale: string;
  verified: boolean;
}

export interface ParsedOrder {
  quantity: number | null;
  deadline_hours: number | null;
  destination: Place | null;
  destination_candidates: Place[];
  destination_text: string | null;
  missing: string[];
  issues: string[];
  notes: string;
}

export interface OptimizeRequest {
  destination_lat: number;
  destination_lng: number;
  destination_name: string;
  quantity: number;
  deadline_hours: number;
}

export interface WeatherStatus {
  source: string;
  available: boolean;
  fetched_at: number | null;
  grid_points?: number;
  max_precip_mm: number | null;
  mean_precip_mm: number | null;
  error: string | null;
}

export interface Meta {
  network: {
    built_at: string;
    bbox_s_w_n_e: number[] | null;
    sources: { roads: string; elevation: string; incidents: string };
    counts: { nodes: number; edges: number; places: number };
    network_km: number;
    surface_inferred_fraction: number;
    max_piece_km: number;
    is_fixture?: boolean;
  };
  weather: Partial<WeatherStatus> & { available: boolean };
  warehouses: number;
  reasoner: "gemma" | "deterministic";
  ai_features: {
    explanations: boolean;
    order_intake: boolean;
    remedies: boolean;
    required: boolean;
  };
}

export interface Warehouse {
  node_id: string;
  name: string;
  lat: number;
  lng: number;
  units_in_stock: number;
  vehicles_available: number;
  vehicle_capacity: number;
}

export interface Place {
  id: string;
  name: string;
  lat: number;
  lng: number;
  kind: string;
}

export interface NetworkGeoJSON {
  type: "FeatureCollection";
  bbox: [number, number, number, number]; // south, west, north, east
  features: {
    geometry: { type: "LineString"; coordinates: [number, number][] }; // [lng, lat]
    properties: {
      road_type: string;
      highway: string;
      name: string;
      length_km: number;
      slope_pct: number;
      hazard: number;
    };
  }[];
}

/** What POST /optimize-route/{id}/ai adds to an already-returned answer. */
export type AiPatch = Partial<
  Pick<
    OptimizeResponse,
    | "explanation"
    | "explanation_source"
    | "risk_note"
    | "risk_note_source"
    | "remedies"
    | "chosen_plan"
    | "alternatives"
    | "alternatives_total"
    | "chosen_matches_policy"
  >
> & { ai: { status: "done" | "partial"; error?: string } };

export type Role = "viewer" | "member" | "admin" | "owner";
export const ROLE_RANK: Record<Role, number> = { viewer: 1, member: 2, admin: 3, owner: 4 };

export interface Me {
  user: { id: string; email: string; name: string };
  organisation: { id: string; name: string };
  role: Role;
}

export interface OrderRecord {
  id: string;
  created_at: string;
  by: string | null;
  destination: string;
  lat: number;
  lng: number;
  quantity: number;
  deadline_hours: number;
  status: "feasible" | "infeasible";
  plan_type: PlanType | null;
  sources: string[];
  eta_hours: number | null;
  p_late: number | null;
}

export interface Closure {
  id: string;
  lat: number;
  lng: number;
  radius_m: number;
  reason: string;
  until: string | null;
}

export interface OrgSettings {
  organisation: string;
  loading_minutes: number;
}

export interface Team {
  members: {
    user_id: string;
    email: string;
    name: string;
    role: Role;
    last_login_at: string | null;
  }[];
  invites: { id: string; email: string; role: Role; expires_at: string }[];
}

export interface AuditEntry {
  ts: string;
  user: string | null;
  action: string;
  target: string;
  detail: Record<string, unknown>;
}
