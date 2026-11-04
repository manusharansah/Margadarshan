# Data sources

| Layer | Source | Used for |
|---|---|---|
| Roads, surface, speed limits | Overture Maps transportation theme (open, largely OpenStreetMap-derived, ODbL) or OpenStreetMap through Overpass | Road graph and road type |
| Place names | Overture divisions, with English and Devanagari names | Destination search |
| Elevation | NASA SRTM 1 arc-second from the AWS Open Data Terrain Tiles bucket | Grade, climb, hillslope angle |
| Rainfall | Open-Meteo forecast API, past 3 days plus next 24 hours | Hazard multiplier |
| Past incidents | Optional CSV with latitude and longitude, such as a BIPAD export. Not loaded in the current build. | Incident proximity score |
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

The evidence behind each model parameter, and what it would take to validate it, is in [MODEL.md](MODEL.md). In short:

* The hazard score is a screening index, not a calibrated probability. No incident catalogue could be loaded here.
* Grade comes from 30 m elevation data smoothed over about 150 m. It can be off by several percent on short stretches.
* 76% of road pieces in the Kathmandu and Kavre build carry no surface tag. Their surface is estimated from tagged roads of
  the same class, which is right for about 84% of held-out tagged roads.
* About 4.5% of road pieces are one-way and are routed as such. Turn restrictions and live traffic are not modelled.
* Road closures are entered by hand for each organisation on the Inventory page.
* Handling time per pickup is an organisation setting that starts at zero.
