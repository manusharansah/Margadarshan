import { useEffect, useRef, useState } from "react";
import type { Map as LeafletMap, LayerGroup } from "leaflet";
import { MAP_CENTER, placeName, project } from "@/lib/gis/geo";
import {
  cachedGeometry,
  edgeKey,
  syntheticRoad,
  warmRoadGeometry,
  type LatLng,
} from "@/lib/gis/roadGeometry";
import type { PlanSummary, RoadEdge, StorageNode } from "@/lib/gis/types";


interface Props {
  nodes: StorageNode[];
  edges: RoadEdge[];
  destination: string | null;
  plan: PlanSummary | null;
  layers: { roads: boolean; hazard: boolean; storages: boolean };
}

/**
 * Renders the backend's road-network graph on a real Nepal basemap.
 * Every geometry drawn here comes from GET /network plus the chosen
 * plan's leg paths — nothing is invented client-side.
 */
export function NetworkMap({ nodes, edges, destination, plan, layers }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const overlayRef = useRef<LayerGroup | null>(null);
  const fittedRef = useRef(false);
  // Bumped whenever more real-road geometry lands, to trigger a redraw.
  const [geomVersion, setGeomVersion] = useState(0);


  useEffect(() => {
    let cancelled = false;

    (async () => {
      const L = await import("leaflet");
      if (cancelled || !containerRef.current || mapRef.current) return;

      const map = L.map(containerRef.current, {
        center: MAP_CENTER,
        zoom: 11,
        zoomControl: false,
        attributionControl: true,
      });
      L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 18,
        attribution: "&copy; OpenStreetMap &copy; CARTO",
      }).addTo(map);
      L.control.zoom({ position: "topright" }).addTo(map);

      mapRef.current = map;
      overlayRef.current = L.layerGroup().addTo(map);
      // Force a size recalc once the panel layout has settled.
      setTimeout(() => map.invalidateSize(), 100);
      fittedRef.current = false;
    })();

    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
      overlayRef.current = null;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const L = await import("leaflet");
      const overlay = overlayRef.current;
      if (cancelled || !overlay) return;
      overlay.clearLayers();

      const byId = new Map(nodes.map((n) => [n.node_id, n]));

      // Frame the whole network once it arrives.
      if (!fittedRef.current && nodes.length) {
        fittedRef.current = true;
        mapRef.current?.fitBounds(
          L.latLngBounds(nodes.map((n) => project(n.x, n.y))),
          { padding: [70, 70] },
        );
      }
      const planEdgeKeys = new Set<string>();
      for (const leg of plan?.legs ?? []) {
        for (let i = 0; i < leg.path.length - 1; i++) {
          planEdgeKeys.add([leg.path[i], leg.path[i + 1]].sort().join("|"));
        }
      }

      /**
       * Real road shape for an edge. Falls back to a terrain-flavoured curve
       * until the OSM snap for that pair has landed. Attributes/weights are
       * untouched — this only changes the drawn geometry.
       */
      const shape = (e: RoadEdge, a: StorageNode, b: StorageNode): LatLng[] => {
        const key = edgeKey(e.node_a, e.node_b);
        const pa = project(a.x, a.y);
        const pb = project(b.x, b.y);
        const hit = cachedGeometry(key);
        if (hit) {
          // Keep both ends anchored on the node markers.
          return [pa, ...hit.slice(1, -1), pb];
        }
        return syntheticRoad(pa, pb, e, key);
      };

      // --- road network -------------------------------------------------
      if (layers.roads) {
        for (const e of edges) {
          const a = byId.get(e.node_a);
          const b = byId.get(e.node_b);
          if (!a || !b) continue;
          const onPlan = planEdgeKeys.has([e.node_a, e.node_b].sort().join("|"));
          if (onPlan) continue;
          L.polyline(shape(e, a, b), {
            color: "#9a9a97",
            weight: e.road_type === "paved" ? 2 : 1.5,
            opacity: 0.55,
            lineJoin: "round",
            lineCap: "round",
            dashArray: e.road_type === "paved" ? undefined : "5 5",
          })
            .bindTooltip(
              `<b>${e.node_a} – ${e.node_b}</b><br/>${e.length_km} km · ${e.road_type}<br/>slope ${e.slope_pct}% · max ${e.max_speed_kmph} km/h<br/>hazard ${e.hazard_score} · cost ${e.weight ?? "–"}`,
              { sticky: true },
            )
            .addTo(overlay);
        }
      }

      // --- hazard exposure ----------------------------------------------
      if (layers.hazard) {
        for (const e of edges) {
          if (e.hazard_score < 0.6) continue;
          const a = byId.get(e.node_a);
          const b = byId.get(e.node_b);
          if (!a || !b) continue;
          L.polyline(shape(e, a, b), {
            color: "#c0562c",
            weight: 4,
            opacity: 0.1 + e.hazard_score * 0.16,
            lineJoin: "round",
            lineCap: "round",
          })
            .bindTooltip(`High hazard exposure: ${e.hazard_score}`, { sticky: true })
            .addTo(overlay);
        }
      }

      // --- chosen plan legs ----------------------------------------------
      const edgeByKey = new Map(edges.map((e) => [edgeKey(e.node_a, e.node_b), e]));
      for (const leg of plan?.legs ?? []) {
        const pts: LatLng[] = [];
        for (let i = 0; i < leg.path.length - 1; i++) {
          const a = byId.get(leg.path[i]!);
          const b = byId.get(leg.path[i + 1]!);
          if (!a || !b) continue;
          const e = edgeByKey.get(edgeKey(a.node_id, b.node_id));
          const pa0 = project(a.x, a.y);
          const pb0 = project(b.x, b.y);
          let seg = e
            ? shape(e, a, b)
            : cachedGeometry(edgeKey(a.node_id, b.node_id)) ??
              syntheticRoad(pa0, pb0, undefined, edgeKey(a.node_id, b.node_id));
          // shape() is orientation-agnostic; align it with travel direction.
          const first = seg[0]!;
          const last = seg[seg.length - 1]!;
          const dFirst = Math.hypot(first[0] - pa0[0], first[1] - pa0[1]);
          const dLast = Math.hypot(last[0] - pa0[0], last[1] - pa0[1]);
          if (dLast < dFirst) seg = [...seg].reverse();
          pts.push(...(pts.length ? seg.slice(1) : seg));
        }
        if (pts.length < 2) continue;
        L.polyline(pts, {
          color: "#ffffff",
          weight: 8,
          opacity: 0.9,
          lineJoin: "round",
          lineCap: "round",
        }).addTo(overlay);
        L.polyline(pts, {
          color: "#1c1c1a",
          weight: 4,
          opacity: 1,
          lineJoin: "round",
          lineCap: "round",
        })
          .bindTooltip(
            `<b>Leg from ${leg.source}</b><br/>${leg.units_carried} units · ${leg.vehicles_used} vehicle(s)<br/>${leg.time_hours.toFixed(2)} h · cost ${leg.edge_cost.toFixed(1)}`,
            { sticky: true },
          )
          .addTo(overlay);
      }


      // --- nodes -----------------------------------------------------------
      if (layers.storages) {
        const sources = new Set(plan?.sources_used.flatMap((s) => s.split("+")) ?? []);
        for (const n of nodes) {
          const isDest = n.node_id === destination;
          const isSource = sources.has(n.node_id);
          const radius = isDest || isSource ? 8 : 5;
          L.circleMarker(project(n.x, n.y), {
            radius,
            color: isDest ? "#1c1c1a" : isSource ? "#1c1c1a" : "#6f6f6c",
            fillColor: isDest ? "#1c1c1a" : isSource ? "#ffffff" : "#ffffff",
            fillOpacity: 1,
            weight: isDest || isSource ? 3 : 1.5,
          })
            .bindTooltip(
              `<b>${placeName(n.node_id)} · ${n.node_id}</b><br/>${n.units_in_stock} units in stock<br/>${n.vehicles_available} vehicle(s) × ${n.vehicle_capacity}${isDest ? "<br/><i>Destination</i>" : isSource ? "<br/><i>Source warehouse</i>" : ""}`,
              { sticky: true },
            )
            .addTo(overlay);

          if (isDest || isSource) {
            L.marker(project(n.x, n.y), {
              icon: L.divIcon({
                className: "",
                html: `<div class="map-node-label${isDest ? " map-node-label-dest" : ""}">${placeName(n.node_id)}</div>`,
                iconSize: [0, 0],
                iconAnchor: [-12, 8],
              }),
              interactive: false,
            }).addTo(overlay);
          }
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [nodes, edges, destination, plan, layers, geomVersion]);

  // Snap every edge onto the real OSM road network in the background,
  // redrawing progressively as geometry arrives.
  useEffect(() => {
    if (!edges.length || !nodes.length) return;
    let cancelled = false;
    const byId = new Map(nodes.map((n) => [n.node_id, n]));
    warmRoadGeometry(
      edges,
      (id) => {
        const n = byId.get(id);
        return n ? project(n.x, n.y) : undefined;
      },
      () => {
        if (!cancelled) setGeomVersion((v) => v + 1);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [nodes, edges]);


  return <div ref={containerRef} className="h-full w-full" />;
}