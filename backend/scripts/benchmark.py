"""Scale check: planner latency on a large lattice (algorithm benchmark, not data)."""
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gis_backend.models import DeliveryRequest, StorageNode  # noqa: E402
from gis_backend.network import Network  # noqa: E402
from gis_backend.service import process_request  # noqa: E402
from tests.helpers import LAT0, LNG0, make_artifact  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 120
xy = {r * N + c: (r * 0.4, c * 0.4) for r in range(N) for c in range(N)}
edges = []
for r in range(N):
    for c in range(N):
        i = r * N + c
        for j in ((i + 1) if c < N - 1 else None, (i + N) if r < N - 1 else None):
            if j is not None:
                k = (i * 7 + j * 3) % 5
                edges.append((i, j, dict(road_type=["paved", "gravel", "rough", "paved", "gravel"][k],
                                         hazard_terrain=[0, .2, .9, .1, .4][k], slope_pct=[2, 6, 12, 3, 8][k])))
t = time.time()
net = Network(make_artifact(edges, xy))
print(f"graph: {net.graph.number_of_nodes()} nodes / {net.graph.number_of_edges()} edges, built in {time.time() - t:.2f}s")
ws = [StorageNode(f"W{i}", f"W{i}", LAT0 + (i * 3 % N) * 0.4 / 111.32, LNG0 + (i * 17 % N) * 0.4 / 98.5, 300, 2) for i in range(8)]
for label, qty, hrs in (("single-source", 200, 48), ("multi-source", 1100, 48), ("infeasible-deadline", 200, 0.1)):
    times = []
    for _ in range(3):
        t = time.time()
        out = process_request(net, ws, DeliveryRequest(LAT0 + 20 * 0.4 / 111.32, LNG0 + 90 * 0.4 / 98.5, qty, hrs))
        times.append(time.time() - t)
    print(f"{label:20s} status={out['status']:10s} candidates={out['candidates_evaluated']:3d} median={statistics.median(times):.2f}s")
