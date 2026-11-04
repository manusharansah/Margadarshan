# MargaAI backend

A FastAPI service and the offline data pipeline. See the [root README](../README.md), the
[architecture](../docs/ARCHITECTURE.md) and the [data sources](../docs/DATA_SOURCES.md).

```bash
pip install -r requirements-dev.txt -r requirements-ingest.txt
python -m pytest -q -m "not live"          # 263 tests. Real-elevation tests download one public tile and skip offline.
python -m pytest -q -m live                # real Gemma calls, needs GEMMA_API_KEY, slow
python -m gis_backend.ingest --help        # build network.json.gz
python scripts/dev_bootstrap.py            # development organisation, invitations and demo warehouses
python -m gis_backend.seed_warehouses f.csv --org "Acme Ltd"
uvicorn gis_backend.api:app --port 8000    # API docs at /docs
python scripts/benchmark.py 120            # planner speed on a 14,400-node graph
```

Environment variables are listed with comments in `.env.example`. The main groups are data (`MARGA_DATA_DIR`,
`DATABASE_URL`, `MARGA_NETWORK_S3_URI`), sign-in (`OIDC_*`, `MARGA_PUBLIC_URL`, `MARGA_COOKIE_SECURE`,
`MARGA_BOOTSTRAP_OWNER_EMAIL`, `MARGA_ALLOWED_EMAIL_DOMAINS`, `MARGA_ALLOW_SELF_SIGNUP`), retention
(`MARGA_ORDER_RETENTION_DAYS`, `MARGA_AUDIT_RETENTION_DAYS`) and AI (`GEMMA_API_KEY`, `GEMMA_MODEL`, `MARGA_REQUIRE_GEMMA`).
