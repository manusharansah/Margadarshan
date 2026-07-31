import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";
import {
  buildGraph,
  getLocalNetwork,
  processRequest,
} from "./engine";
import type { NetworkResponse, OptimizeResponse, RoadEdge, StorageNode } from "./types";

/**
 * Backend source of truth.
 *
 * If GIS_API_BASE is set (e.g. http://localhost:8000 or an ngrok URL for the
 * FastAPI service in gis_backend/), every call is proxied to the real Python
 * backend — GET /network and POST /optimize-route, unchanged.
 *
 * If it is not set, an in-process TypeScript port of the same engine runs so
 * the demo works standalone. The response shape is identical either way.
 */

const RouteRequestIn = z.object({
  destination: z.string().min(1),
  quantity: z.number().int().positive(),
  deadline_hours: z.number().positive(),
});

export const getNetwork = createServerFn({ method: "GET" }).handler(
  async (): Promise<NetworkResponse & { backend: "live_fastapi" | "local_port" }> => {
    const base = process.env["GIS_API_BASE"]?.replace(/\/$/, "");
    if (base) {
      const res = await fetch(`${base}/network`);
      if (!res.ok) {
        throw new Error(`Backend GET /network failed [${res.status}]: ${await res.text()}`);
      }
      const data = (await res.json()) as NetworkResponse;
      return { ...data, backend: "live_fastapi" };
    }
    const { network } = getLocalNetwork();
    return { ...network, backend: "local_port" };
  },
);

export const optimizeRoute = createServerFn({ method: "POST" })
  .inputValidator((input: unknown) => RouteRequestIn.parse(input))
  .handler(async ({ data }): Promise<OptimizeResponse> => {
    const base = process.env["GIS_API_BASE"]?.replace(/\/$/, "");

    if (base) {
      const res = await fetch(`${base}/optimize-route`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const body = (await res.json()) as OptimizeResponse;
      // 503 = reasoning_unavailable: routing math is valid, Gemma is down.
      if (!res.ok && res.status !== 503) {
        throw new Error(`Backend POST /optimize-route failed [${res.status}]`);
      }
      return body;
    }

    const { nodes, graph } = getLocalNetwork();
    if (!nodes[data.destination]) {
      throw new Error(
        `Unknown destination '${data.destination}'. Known nodes: ${Object.keys(nodes).sort().join(", ")}`,
      );
    }
    return processRequest(nodes, graph, data);
  });

/** Re-exported so the client can rebuild the graph for map drawing. */
export function graphFromNetwork(nodes: StorageNode[], edges: RoadEdge[]) {
  const byId = Object.fromEntries(nodes.map((n) => [n.node_id, n]));
  return buildGraph(byId, edges);
}
