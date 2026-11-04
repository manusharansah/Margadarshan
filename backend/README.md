# MargaAI backend

A FastAPI service and the offline data pipeline. See the [root README](../README.md), the
[architecture](../docs/ARCHITECTURE.md) and the [data sources](../docs/DATA_SOURCES.md).

```bash
pip install -r requirements-dev.txt -r requirements-ingest.txt
python -m pytest -q                        # 114 tests. The real-elevation tests download one public tile and skip offline.
python -m gis_backend.ingest --help        # build network.json.gz
python -m gis_backend.seed_warehouses f.csv
uvicorn gis_backend.api:app --port 8000    # API docs at /docs
python scripts/benchmark.py 120            # planner speed on a 14,400-node graph
```

Environment variables: `MARGA_DATA_DIR`, `MARGA_NETWORK_FILE`, `DATABASE_URL`, `MARGA_ADMIN_API_KEY`, `GEMMA_API_KEY`,
`GEMMA_MODEL`, `MARGA_REQUIRE_GEMMA`, `CORS_ORIGINS`, `MARGA_WEATHER_ENABLED`, `MARGA_WEATHER_TTL`, `MARGA_LOADING_HOURS`
and `MARGA_NETWORK_S3_URI`.
