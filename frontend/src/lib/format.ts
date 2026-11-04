export function formatHours(h: number): string {
  if (h < 1 / 60) return "under 1 min";
  const total = Math.round(h * 60);
  const hh = Math.floor(total / 60);
  const mm = total % 60;
  return hh === 0 ? `${mm} min` : mm === 0 ? `${hh} h` : `${hh} h ${mm} min`;
}

export const formatKm = (km: number) => (km < 10 ? `${km.toFixed(1)} km` : `${Math.round(km)} km`);
export const formatLitres = (l: number) => `${l < 10 ? l.toFixed(1) : Math.round(l)} L`;
export const formatInt = (n: number) => n.toLocaleString("en-US");

export type HazardLevel = { label: "Low" | "Moderate" | "High" | "Severe"; colour: string };

/** Colour scale shared by the map and the result cards (0 = safe, 1 = severe). */
export function hazardLevel(h: number): HazardLevel {
  if (h < 0.25) return { label: "Low", colour: "#4f9d69" };
  if (h < 0.5) return { label: "Moderate", colour: "#d9a02b" };
  if (h < 0.75) return { label: "High", colour: "#e0702d" };
  return { label: "Severe", colour: "#c7382f" };
}

export const PLAN_LABEL: Record<string, string> = {
  single_source: "Single warehouse",
  multi_source_parallel: "Several warehouses in parallel",
  single_vehicle_multistop: "One vehicle, multi-stop pickup",
};

export function haversineKm(a: [number, number], b: [number, number]): number {
  const R = 6371.0088;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b[0] - a[0]);
  const dLng = toRad(b[1] - a[1]);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
