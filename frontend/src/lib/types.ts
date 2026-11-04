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
}

export interface OptimizeResponse {
  status: "feasible" | "infeasible";
  explanation: string;
  explanation_source: "gemma" | "deterministic";
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
