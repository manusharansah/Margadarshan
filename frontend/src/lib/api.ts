import { useQuery } from "@tanstack/react-query";
import type {
  AiPatch,
  AuditEntry,
  Closure,
  Me,
  Meta,
  OrderRecord,
  OrgSettings,
  Team,
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

/** Every call carries a header a cross-site form cannot set; together with the SameSite cookie it blocks CSRF. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    credentials: "same-origin",
    headers: {
      "X-Requested-With": "marga",
      ...(init?.headers as Record<string, string> | undefined),
    },
  });
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

export const send = <T = unknown>(
  method: "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
) =>
  request<T>(path, {
    method,
    ...(body === undefined
      ? {}
      : { headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  });

export const uploadWarehouses = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<{ imported: number }>("/warehouses/import", { method: "POST", body: form });
};

export const signOut = () => request<{ signed_out: boolean }>("/auth/logout", { method: "POST" });
export const fetchExport = () => request<unknown>("/me/export");

export const useMe = () =>
  useQuery({
    queryKey: ["me"],
    queryFn: () => request<Me>("/auth/me"),
    staleTime: 60_000,
    retry: (count, err) => !(err instanceof ApiError && err.status === 401) && count < 1,
  });

/** A GET that returns a JSON body of type B, of which the page wants the part picked by `pick`. */
function useGet<B, T>(key: string, path: string, pick: (body: B) => T, enabled = true) {
  return useQuery({
    queryKey: [key],
    queryFn: async () => pick(await request<B>(path)),
    enabled,
    staleTime: 15_000,
  });
}

export const useOrders = () =>
  useGet<{ orders: OrderRecord[] }, OrderRecord[]>("orders", "/orders", (b) => b.orders);
export const useClosures = () =>
  useGet<{ closures: Closure[] }, Closure[]>("closures", "/closures", (b) => b.closures);
export const useSettings = () =>
  useGet<OrgSettings, OrgSettings>("settings", "/settings", (b) => b);
export const useTeam = () => useGet<Team, Team>("team", "/team", (b) => b);
export const useAudit = (enabled: boolean) =>
  useGet<{ entries: AuditEntry[] }, AuditEntry[]>("audit", "/audit", (b) => b.entries, enabled);

export const optimizeRoute = (body: OptimizeRequest) =>
  request<OptimizeResponse>("/optimize-route", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

export const optimizeRouteAi = (resultId: string) =>
  request<AiPatch>(`/optimize-route/${resultId}/ai`, { method: "POST" });

export const parseOrder = (text: string) =>
  request<ParsedOrder>("/parse-order", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });

export const useMeta = (enabled = true) =>
  useQuery({
    queryKey: ["meta"],
    queryFn: () => request<Meta>("/meta"),
    staleTime: 60_000,
    retry: 1,
    enabled,
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
