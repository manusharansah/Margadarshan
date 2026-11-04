# Inventory format (for any operator)

MargaAI is operator-agnostic: it knows nothing about your business until you give it warehouses. Anyone can use it by
supplying one CSV. A ready example is [`backend/examples/warehouses.sample.csv`](../backend/examples/warehouses.sample.csv)
— **sample data only** (every id starts with `SAMPLE-`; stock and fleet numbers are invented to show the format).
A larger demo set, [`warehouses.demo.csv`](../backend/examples/warehouses.demo.csv) (14 depots: big valley hubs, small hill depots,
one depot with stock but no vehicles; ids start `DEMO-`), exercises every plan type. Both are **invented** — replace them with your own
data before any real use.

| Column | Required | Meaning | Rules |
|---|---|---|---|
| `id` | yes | Your stable code for the warehouse | unique; re-importing the same id updates it |
| `name` | yes | Human label shown on the map and in explanations | — |
| `lat`, `lng` | yes | Warehouse location (WGS84 decimal degrees) | must be within 5 km of a mapped road |
| `units_in_stock` | yes | Units available to ship now | integer ≥ 0 |
| `vehicles_available` | yes | Vehicles free to dispatch now | integer ≥ 0 |
| `vehicle_capacity` | no (default 200) | Units one vehicle carries | integer > 0 |

Import is **all-or-nothing**: one bad row rejects the file and names the line.

```bash
python -m gis_backend.seed_warehouses my_inventory.csv                       # local
curl -H "X-API-Key: $KEY" -F file=@my_inventory.csv $API/warehouses/import   # deployed
curl -H "X-API-Key: $KEY" -X PUT $API/warehouses/ID -H 'content-type: application/json' \
     -d '{"name":"…","lat":27.6,"lng":85.5,"units_in_stock":500,"vehicles_available":2}'   # single update
```

Keep stock current (re-import after each dispatch or from your stock system) — recommendations are only as good as it is.
Several operators? Run one deployment per operator (separate database); multi-tenant accounts are not built.
