# Data sources & how to load real data

| Layer | Source | Licence | Used for |
|---|---|---|---|
| Roads, surface, speed, place names | OpenStreetMap via Overpass API | ODbL | graph, road type, search |
| Elevation | NASA SRTM 1-arc-second (AWS Open Data "Terrain Tiles", `elevation-tiles-prod`) | public domain | grade, ascent/descent, hillslope |
| Rainfall | Open-Meteo forecast API (past 3 d + next 24 h) | CC-BY 4.0, non-commercial free tier | hazard amplification |
| Past incidents (optional) | Any CSV with `latitude,longitude` — e.g. BIPAD portal export, NASA Global Landslide Catalog | per source | incident proximity |
| Warehouses & fleet | **You** | — | the only business data; no public source exists |

## Build the real network

Outbound access is needed to `overpass-api.de` (or `overpass.kumi.systems`) and `elevation-tiles-prod.s3.amazonaws.com`.

```bash
cd backend
python -m gis_backend.ingest --bbox 27.45 85.25 27.80 85.80 --out data/network.json.gz \
        [--incidents landslides.csv]
# Behind a firewall? Save the Overpass response elsewhere and pass --overpass-json saved.json
```

Output metadata (visible on the **Data** page) records built-at, counts, kilometres, and the **fraction of roads whose
surface was inferred** because OSM has no `surface` tag. Large areas grow quickly — start with one district.

## Load inventory

CSV columns: `id,name,lat,lng,units_in_stock,vehicles_available[,vehicle_capacity]` (sample:
`backend/examples/warehouses.sample.csv` — sample values, replace them).

```bash
python -m gis_backend.seed_warehouses my_inventory.csv         # local
curl -H "X-API-Key: $KEY" -F file=@my_inventory.csv $API/warehouses/import   # deployed (all-or-nothing)
```

## Known limitations

* **Hazard** = 0.6 × terrain + 0.4 × incident proximity (terrain only without incidents), × (1 + 1.5 × rain index).
  Terrain = hillslope 15°→45° ramp; incident = 1 − e^(−n/3) within 2 km; rain index = mm/200 over 4 days. These are
  documented heuristics, **not** a calibrated landslide model; flood exposure is not modelled separately.
* **Grade** from 30 m SRTM, smoothed over ~150 m; can be off by several % on short stretches.
* **Surface/speed**: OSM tags when present, otherwise class defaults (reported as "surface inferred").
* **Not modelled**: one-way roads, turn restrictions, live traffic/closures, vehicle-specific speeds, loading time
  (set `MARGA_LOADING_HOURS` to add per-pickup handling).
* **Fuel** uses an assumed 0.30 L/km base for a loaded ~8 t truck — calibrate with fleet logs.
