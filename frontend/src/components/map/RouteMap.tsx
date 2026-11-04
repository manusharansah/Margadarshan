import { useEffect, useRef } from "react";
import type { Map as LeafletMap, LayerGroup, LatLngBoundsExpression } from "leaflet";
import "leaflet/dist/leaflet.css";
import type { NetworkGeoJSON, Plan, Warehouse } from "@/lib/types";

export interface Pin {
  name: string;
  lat: number;
  lng: number;
}

interface Props {
  network: NetworkGeoJSON | undefined;
  warehouses: Warehouse[];
  destination: Pin | null;
  plan: Plan | null;
  layers: { roads: boolean; hazard: boolean; storages: boolean };
  onPickDestination: (lat: number, lng: number) => void;
}

const INK = "#1c1c1a";
const MAX_TOOLTIP_ROADS = 3000; // keep hover tooltips only while the layer is small enough to stay fast

/**
 * Leaflet map in the original MargaAI style (grey dashed roads, terracotta hazard overlay,
 * white-haloed dark route, ringed warehouse markers). Everything drawn comes from the backend:
 * roads = GET /network, route = the plan's leg geometry, markers = inventory records.
 */
export function RouteMap({
  network,
  warehouses,
  destination,
  plan,
  layers,
  onPickDestination,
}: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<LeafletMap | null>(null);
  const roads = useRef<LayerGroup | null>(null);
  const overlay = useRef<LayerGroup | null>(null);
  const fitted = useRef(false);
  const resizeTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const pick = useRef(onPickDestination);
  pick.current = onPickDestination;

  useEffect(() => {
    let dead = false;
    void (async () => {
      const L = await import("leaflet");
      if (dead || !el.current || map.current) return;
      const m = L.map(el.current, {
        zoomControl: false,
        preferCanvas: true,
        zoomAnimation: false,
        fadeAnimation: false,
        center: [27.65, 85.5],
        zoom: 10,
      });
      L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 18,
        attribution: "&copy; OpenStreetMap &copy; CARTO",
      }).addTo(m);
      L.control.zoom({ position: "topright" }).addTo(m);
      m.on("click", (e) => pick.current(e.latlng.lat, e.latlng.lng));
      roads.current = L.layerGroup().addTo(m);
      overlay.current = L.layerGroup().addTo(m);
      map.current = m;
      resizeTimer.current = setTimeout(() => map.current?.invalidateSize(), 100);
      el.current.dispatchEvent(new Event("map-ready"));
    })();
    return () => {
      dead = true;
      clearTimeout(resizeTimer.current);
      map.current?.stop(); // cancel any running pan/zoom animation before teardown
      map.current?.remove();
      map.current = null;
    };
  }, []);

  // Draws once the map exists (immediately, or when it signals ready).
  const whenReady = (fn: () => Promise<void>) => {
    let dead = false;
    const go = () => !dead && void fn();
    if (map.current) go();
    else el.current?.addEventListener("map-ready", go, { once: true });
    return () => {
      dead = true;
      el.current?.removeEventListener("map-ready", go);
    };
  };

  // ---- road network + hazard exposure ------------------------------------
  useEffect(
    () =>
      whenReady(async () => {
        const L = await import("leaflet");
        if (!roads.current || !map.current || !network) return;
        roads.current.clearLayers();
        const renderer = L.canvas({ padding: 0.3 });
        const hover = network.features.length <= MAX_TOOLTIP_ROADS;
        const ll = (c: [number, number][]) => c.map(([lng, lat]) => [lat, lng] as [number, number]);

        if (layers.roads) {
          for (const f of network.features) {
            const p = f.properties;
            const line = L.polyline(ll(f.geometry.coordinates), {
              renderer,
              color: "#9a9a97",
              weight: p.road_type === "paved" ? 2 : 1.5,
              opacity: 0.55,
              lineJoin: "round",
              lineCap: "round",
              dashArray: p.road_type === "paved" ? undefined : "5 5",
              interactive: hover,
            });
            if (hover)
              line.bindTooltip(
                `<b>${esc(p.name || p.highway)}</b><br/>${p.length_km} km · ${p.road_type}<br/>grade ${p.slope_pct}% · hazard ${p.hazard}`,
                { sticky: true },
              );
            line.addTo(roads.current);
          }
        }
        if (layers.hazard) {
          for (const f of network.features) {
            if (f.properties.hazard < 0.6) continue;
            L.polyline(ll(f.geometry.coordinates), {
              renderer,
              color: "#c0562c",
              weight: 4,
              opacity: 0.1 + f.properties.hazard * 0.16,
              lineJoin: "round",
              lineCap: "round",
              interactive: false,
            }).addTo(roads.current);
          }
        }
        if (!fitted.current) {
          fitted.current = true;
          const [s, w, n, e] = network.bbox;
          map.current.fitBounds(
            [
              [s, w],
              [n, e],
            ] as LatLngBoundsExpression,
            { padding: [70, 70], animate: false },
          );
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [network, layers.roads, layers.hazard],
  );

  // ---- route, warehouses, destination -------------------------------------
  useEffect(
    () =>
      whenReady(async () => {
        const L = await import("leaflet");
        if (!overlay.current || !map.current) return;
        const g = overlay.current;
        g.clearLayers();

        for (const leg of plan?.legs ?? []) {
          const pts = leg.geometry.map(([la, ln]) => [la, ln] as [number, number]);
          if (pts.length < 2) continue;
          L.polyline(pts, {
            color: "#ffffff",
            weight: 8,
            opacity: 0.9,
            lineJoin: "round",
            lineCap: "round",
            interactive: false,
          }).addTo(g);
          L.polyline(pts, {
            color: INK,
            weight: 4,
            opacity: 1,
            lineJoin: "round",
            lineCap: "round",
          })
            .bindTooltip(
              `<b>Leg from ${esc(leg.source)}</b><br/>${leg.units_carried} units · ${leg.vehicles_used} vehicle(s)<br/>${leg.time_hours.toFixed(2)} h · cost ${leg.edge_cost.toFixed(1)}`,
              { sticky: true },
            )
            .addTo(g);
        }

        if (layers.storages) {
          const sources = new Set(plan?.sources_used ?? []);
          for (const w of warehouses) {
            const isSource = sources.has(w.node_id);
            L.circleMarker([w.lat, w.lng], {
              radius: isSource ? 8 : 5,
              color: isSource ? INK : "#6f6f6c",
              fillColor: "#ffffff",
              fillOpacity: 1,
              weight: isSource ? 3 : 1.5,
            })
              .bindTooltip(
                `<b>${esc(w.name)}</b><br/>${w.units_in_stock.toLocaleString()} units in stock<br/>${w.vehicles_available} vehicle(s) × ${w.vehicle_capacity}${isSource ? "<br/><i>Source warehouse</i>" : ""}`,
                { sticky: true },
              )
              .addTo(g);
            if (isSource)
              L.marker([w.lat, w.lng], {
                icon: L.divIcon({
                  className: "",
                  html: `<div class="map-node-label">${esc(w.name)}</div>`,
                  iconSize: [0, 0],
                  iconAnchor: [-12, 8],
                }),
                interactive: false,
              }).addTo(g);
          }
        }

        if (destination) {
          L.circleMarker([destination.lat, destination.lng], {
            radius: 8,
            color: INK,
            fillColor: INK,
            fillOpacity: 1,
            weight: 3,
          })
            .bindTooltip(`<b>${esc(destination.name)}</b><br/><i>Destination</i>`, { sticky: true })
            .addTo(g);
          L.marker([destination.lat, destination.lng], {
            icon: L.divIcon({
              className: "",
              html: `<div class="map-node-label map-node-label-dest">${esc(destination.name)}</div>`,
              iconSize: [0, 0],
              iconAnchor: [-12, 8],
            }),
            interactive: false,
          }).addTo(g);
        }

        if (plan) {
          const all = plan.legs.flatMap((l) =>
            l.geometry.map(([la, ln]) => [la, ln] as [number, number]),
          );
          if (all.length)
            map.current.fitBounds(all, { padding: [70, 70], maxZoom: 14, animate: false });
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [warehouses, destination, plan, layers.storages],
  );

  return <div ref={el} className="size-full" role="application" aria-label="Delivery map" />;
}

function esc(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!,
  );
}
