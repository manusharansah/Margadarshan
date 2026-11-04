import { useQuery } from "@tanstack/react-query";
import type {
  Meta,
  NetworkGeoJSON,
  OptimizeRequest,
  OptimizeResponse,
  ParsedOrder,
  Place,
  Warehouse,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export const optimizeRoute = (body: OptimizeRequest) =>
  request<OptimizeResponse>("/optimize-route", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

export const parseOrder = (text: string) =>
  request<ParsedOrder>("/parse-order", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });

export const useMeta = () =>
  useQuery({
    queryKey: ["meta"],
    queryFn: () => request<Meta>("/meta"),
    staleTime: 60_000,
    retry: 1,
  });

export const useNetwork = () =>
  useQuery({
    queryKey: ["network"],
    queryFn: () => request<NetworkGeoJSON>("/network"),
    staleTime: 5 * 60_000,
    retry: 1,
  });

export const useWarehouses = () =>
  useQuery({
    queryKey: ["warehouses"],
    queryFn: async () => (await request<{ warehouses: Warehouse[] }>("/warehouses")).warehouses,
    staleTime: 30_000,
  });

export const usePlaces = (q: string) =>
  useQuery({
    queryKey: ["places", q],
    queryFn: async () =>
      (await request<{ places: Place[] }>(`/places?q=${encodeURIComponent(q)}`)).places,
    enabled: q.trim().length >= 2,
    staleTime: 5 * 60_000,
  });
