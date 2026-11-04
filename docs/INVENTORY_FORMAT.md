# Inventory format

MargaAI knows nothing about your business until you give it warehouses. Anyone can use it by supplying one CSV.

Two example files ship in `backend/examples/`. Both are invented, so replace them before real use.

* `warehouses.sample.csv` has 10 depots whose ids start with `SAMPLE-`. It shows the format.
* `warehouses.demo.csv` has 14 depots whose ids start with `DEMO-`. It has large valley hubs, small hill depots, and one depot with stock but no vehicles, so every plan type can occur.

| Column | Required | Meaning |
|---|---|---|
| `id` | yes | Your stable code for the warehouse. Importing the same id again updates it. |
| `name` | yes | The label shown on the map and in explanations. |
| `lat`, `lng` | yes | Location in decimal degrees. It must lie within 5 km of a mapped road. |
| `units_in_stock` | yes | Units available to ship now. An integer, zero or more. |
| `vehicles_available` | yes | Vehicles free to dispatch now. An integer, zero or more. |
| `vehicle_capacity` | no | Units one vehicle carries. The default is 200. |

An import is all or nothing. One bad row rejects the file and names the line.

Admins can add, edit and delete warehouses and upload a CSV on the Inventory page. Other options:

```bash
python -m gis_backend.seed_warehouses my_inventory.csv --org "Acme Ltd"       # organisation must exist already
curl -b cookies.txt -H 'X-Requested-With: marga' -F file=@my_inventory.csv $APP/api/warehouses/import
```

An organisation exists once its first owner has signed in. Each organisation sees only its own warehouses.

Keep stock current. Re-import after each dispatch, or feed the file from your stock system. Recommendations are only as
good as this data.
