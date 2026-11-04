# Data sources

| Layer | Source | Used for |
|---|---|---|
| Roads, surface, speed limits | Overture Maps transportation theme (open, largely OpenStreetMap-derived, ODbL) or OpenStreetMap through Overpass | Road graph and road type |
| Place names | Overture divisions, with English and Devanagari names | Destination search |
| Elevation | NASA SRTM 1 arc-second from the AWS Open Data Terrain Tiles bucket | Grade, climb, hillslope angle |
| Rainfall | Open-Meteo forecast API, past 3 days plus next 24 hours | Hazard multiplier |
| Past incidents | Optional CSV with latitude and longitude, such as a BIPAD export | Incident proximity score |
| Warehouses and fleet | You | Stock and vehicles. No public source exists. |

## Build the road network

```bash
cd backend
pip install -r requirements-ingest.txt
python -m gis_backend.ingest --bbox 27.45 85.25 27.80 85.80 --out data/network.json.gz
```

The default source is Overture Maps. It needs no API server. The tool reads each Parquet file's footer, picks the
row groups that overlap your area, and downloads only those. For Kathmandu and Kavre that is about 9 row groups.

Options:

* `--source overpass` uses OpenStreetMap through the Overpass API instead. Pass `--overpass-json saved.json` to use a saved response.
* `--all-road-classes` adds residential lanes and tracks. The network grows by roughly 70%, and the map and queries slow down.
* `--incidents landslides.csv` adds the incident score.
* `--overture-release 2026-10-21.0` pins a release.

The Kathmandu and Kavre build produced 27,018 junctions, 28,549 road pieces and 4,806 km in about two minutes.
The metadata on the Data page lists the sources, the build time, and the share of roads with an inferred surface.

## Load inventory

See [INVENTORY_FORMAT.md](INVENTORY_FORMAT.md).

## Known limits

* The hazard score is 0.6 times the terrain score plus 0.4 times the incident score, or the terrain score alone
  when there is no incident data. Rain then multiplies it by up to 2.5. The terrain score ramps from 0 at a 15 degree
  hillslope to 1 at 45 degrees. The incident score is 1 minus e to the power of minus n over 3, for n incidents within
  2 km. The rain index is rainfall in millimetres divided by 200. These are documented heuristics. Flooding is not
  modelled separately.
* Grade comes from 30 m elevation data smoothed over about 150 m. It can be off by several percent on short stretches.
* In the Kathmandu and Kavre build, 76% of road pieces carry no surface tag. Their surface comes from the road class.
* One-way roads, turn restrictions, live traffic and closures are not modelled.
* Fuel assumes 0.30 L/km for a loaded 8 tonne truck on flat paved road, plus the energy to climb. Replace the figure with your own logs.
* Set `MARGA_LOADING_HOURS` to add handling time per pickup. The default is zero.
