/**
 * The backend's storage nodes live in a synthetic 0-100 coordinate space
 * (simulate.py). To render them on a real Nepal basemap we project that
 * grid onto a bounding box covering the Kavrepalanchok / Banepa–Dhulikhel
 * corridor. Positions are therefore illustrative geography over real
 * terrain — the attributes themselves are the backend's own values.
 */

export const KAVRE_BBOX = {
  minLng: 85.36,
  maxLng: 85.78,
  minLat: 27.48,
  maxLat: 27.79,
};

export const MAP_CENTER: [number, number] = [
  (KAVRE_BBOX.minLat + KAVRE_BBOX.maxLat) / 2,
  (KAVRE_BBOX.minLng + KAVRE_BBOX.maxLng) / 2,
];

/** x,y in 0..100 -> [lat, lng] */
export function project(x: number, y: number): [number, number] {
  const lng = KAVRE_BBOX.minLng + (x / 100) * (KAVRE_BBOX.maxLng - KAVRE_BBOX.minLng);
  const lat = KAVRE_BBOX.minLat + (y / 100) * (KAVRE_BBOX.maxLat - KAVRE_BBOX.minLat);
  return [lat, lng];
}

/** Stable human-friendly label for a synthetic node id. */
const PLACE_NAMES = [
  "Banepa",
  "Dhulikhel",
  "Panauti",
  "Namobuddha",
  "Nagarkot",
  "Panchkhal",
  "Dhungkharka",
  "Mandan",
  "Sanga",
  "Kushadevi",
  "Bethanchok",
  "Temal",
];

export function placeName(nodeId: string): string {
  const idx = Number.parseInt(nodeId.replace(/\D/g, ""), 10);
  return Number.isFinite(idx) && PLACE_NAMES[idx % PLACE_NAMES.length]
    ? PLACE_NAMES[idx % PLACE_NAMES.length]!
    : nodeId;
}

export function nodeLabel(nodeId: string): string {
  return `${placeName(nodeId)} (${nodeId})`;
}
