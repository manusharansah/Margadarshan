# MargaAI backend

FastAPI service + offline data pipeline. See the [root README](../README.md), [architecture](../docs/ARCHITECTURE.md) and
[data sources](../docs/DATA_SOURCES.md).

```bash
pip install -r requirements-dev.txt
python -m pytest -q                       # 91 tests; real-SRTM tests download one public tile (skip offline)
python -m gis_backend.ingest --help       # build network.json.gz from OSM + SRTM
python -m gis_backend.seed_warehouses f.csv
uvicorn gis_backend.api:app --port 8000   # OpenAPI docs at /docs
python scripts/benchmark.py 120           # planner latency on a 14k-node graph
```

Environment: `MARGA_DATA_DIR`, `MARGA_NETWORK_FILE`, `DATABASE_URL`, `MARGA_ADMIN_API_KEY`, `GEMMA_API_KEY`, `GEMMA_MODEL`,
`CORS_ORIGINS`, `MARGA_WEATHER_ENABLED`, `MARGA_WEATHER_TTL`, `MARGA_LOADING_HOURS`, `MARGA_NETWORK_S3_URI`.
