import { useEffect, useRef } from "react";
import type { Map as LeafletMap, LayerGroup, LatLngBoundsExpression } from "leaflet";
import "leaflet/dist/leaflet.css";
import { hazardLevel } from "@/lib/format";
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
  showHazard: boolean;
  onPickDestination: (lat: number, lng: number) => void;
}

const LEG_COLOURS = ["#2a4fb5", "#7a3fb0", "#0d8a8a", "#b5582a"];

/**
 * Leaflet map. Everything drawn comes from the backend: the road layer is
 * GET /network, the route is the plan's leg geometry (real road polylines),
 * and warehouses are the inventory records.
 */
export function RouteMap({
  network,
  warehouses,
  destination,
  plan,
  showHazard,
  onPickDestination,
}: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<LeafletMap | null>(null);
  const roads = useRef<LayerGroup | null>(null);
  const overlay = useRef<LayerGroup | null>(null);
  const fitted = useRef(false);
  const pick = useRef(onPickDestination);
  pick.current = onPickDestination;

  // ---- create / destroy -------------------------------------------------
  useEffect(() => {
    let dead = false;
    void (async () => {
      const L = await import("leaflet");
      if (dead || !el.current || map.current) return;
      const m = L.map(el.current, {
        zoomControl: false,
        preferCanvas: true,
        center: [27.65, 85.5],
        zoom: 10,
      });
      L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 18,
        attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
      }).addTo(m);
      L.control.zoom({ position: "bottomright" }).addTo(m);
      m.on("click", (e) => pick.current(e.latlng.lat, e.latlng.lng));
      roads.current = L.layerGroup().addTo(m);
      overlay.current = L.layerGroup().addTo(m);
      map.current = m;
      setTimeout(() => m.invalidateSize(), 50);
      el.current.dataset["ready"] = "1";
      el.current.dispatchEvent(new Event("map-ready"));
    })();
    return () => {
      dead = true;
      map.current?.remove();
      map.current = null;
    };
  }, []);

  // ---- road network -----------------------------------------------------
  useEffect(() => {
    let dead = false;
    const draw = async () => {
      const L = await import("leaflet");
      if (dead || !roads.current || !map.current || !network) return;
      roads.current.clearLayers();
      const renderer = L.canvas({ padding: 0.3 });
      for (const f of network.features) {
        const latlngs = f.geometry.coordinates.map(([lng, lat]) => [lat, lng] as [number, number]);
        const colour = showHazard ? hazardLevel(f.properties.hazard).colour : "#9aa3b2";
        L.polyline(latlngs, {
          renderer,
          color: colour,
          weight: showHazard ? 3 : 1.6,
          opacity: showHazard ? 0.85 : 0.55,
          interactive: false,
        }).addTo(roads.current);
      }
      if (!fitted.current) {
        fitted.current = true;
        const [s, w, n, e] = network.bbox;
        map.current.fitBounds(
          [
            [s, w],
            [n, e],
          ] as LatLngBoundsExpression,
          { padding: [20, 20] },
        );
      }
    };
    if (map.current) void draw();
    else el.current?.addEventListener("map-ready", () => void draw(), { once: true });
    return () => {
      dead = true;
    };
  }, [network, showHazard]);

  // ---- warehouses, destination, route -----------------------------------
  useEffect(() => {
    let dead = false;
    const draw = async () => {
      const L = await import("leaflet");
      if (dead || !overlay.current || !map.current) return;
      const g = overlay.current;
      g.clearLayers();
      const used = new Set(plan?.sources_used ?? []);

      for (const w of warehouses) {
        const active = used.has(w.node_id);
        const icon = L.divIcon({
          className: "",
          iconSize: [18, 18],
          iconAnchor: [9, 9],
          html: `<div style="width:18px;height:18px;border-radius:4px;background:${active ? "#2a4fb5" : "#fff"};border:2px solid ${active ? "#1b3580" : "#475068"};box-shadow:0 1px 3px rgba(0,0,0,.3);display:grid;place-items:center"><div style="width:6px;height:6px;border-radius:1px;background:${active ? "#fff" : "#475068"}"></div></div>`,
        });
        L.marker([w.lat, w.lng], { icon, keyboard: false })
          .bindTooltip(
            `<strong>${escapeHtml(w.name)}</strong><br>${w.units_in_stock.toLocaleString()} units · ${w.vehicles_available} vehicle(s)`,
            { direction: "top", offset: [0, -8] },
          )
          .addTo(g);
      }

      plan?.legs.forEach((leg, i) => {
        const pts = leg.geometry.map(([la, ln]) => [la, ln] as [number, number]);
        if (pts.length < 2) return;
        L.polyline(pts, { color: "#fff", weight: 9, opacity: 0.95, interactive: false }).addTo(g);
        L.polyline(pts, {
          color: LEG_COLOURS[i % LEG_COLOURS.length]!,
          weight: 5,
          opacity: 0.95,
          interactive: false,
        }).addTo(g);
      });

      if (destination) {
        const icon = L.divIcon({
          className: "",
          iconSize: [26, 34],
          iconAnchor: [13, 32],
          html: `<svg width="26" height="34" viewBox="0 0 26 34"><path d="M13 1C6.4 1 1 6.2 1 12.6 1 21 13 33 13 33s12-12 12-20.4C25 6.2 19.6 1 13 1z" fill="#c7382f" stroke="#fff" stroke-width="2"/><circle cx="13" cy="12.5" r="4.5" fill="#fff"/></svg>`,
        });
        L.marker([destination.lat, destination.lng], { icon, keyboard: false })
          .bindTooltip(escapeHtml(destination.name), { direction: "top", offset: [0, -30] })
          .addTo(g);
      }

      if (plan) {
        const all = plan.legs.flatMap((l) =>
          l.geometry.map(([la, ln]) => [la, ln] as [number, number]),
        );
        if (all.length) map.current.fitBounds(all, { padding: [60, 60], maxZoom: 14 });
      }
    };
    if (map.current) void draw();
    else el.current?.addEventListener("map-ready", () => void draw(), { once: true });
    return () => {
      dead = true;
    };
  }, [warehouses, destination, plan]);

  return <div ref={el} className="size-full" role="application" aria-label="Delivery map" />;
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!,
  );
}
