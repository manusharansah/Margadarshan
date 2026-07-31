"""
Simulated data generation. Per the finalized workflow (no live APIs, no
real-world data fetching at this phase), everything here is randomly
generated but structurally realistic: a connected road network with
per-edge terrain/hazard attributes, and storage nodes with randomized
stock and vehicle counts.
"""
import random
from .models import StorageNode, RoadEdge

ROAD_TYPES = ["paved", "gravel", "rough"]


def simulate_network(num_nodes: int = 12, seed: int = 42, extra_edge_prob: float = 0.22):
    random.seed(seed)

    nodes = {}
    for i in range(num_nodes):
        node_id = f"W{i}"
        nodes[node_id] = StorageNode(
            node_id=node_id,
            x=round(random.uniform(0, 100), 1),
            y=round(random.uniform(0, 100), 1),
            units_in_stock=random.randint(0, 900),
            vehicles_available=random.randint(0, 4),
        )

    node_ids = list(nodes.keys())
    random.shuffle(node_ids)

    edges = []
    seen_pairs = set()

    def add_edge(a, b):
        pair = tuple(sorted((a, b)))
        if pair in seen_pairs or a == b:
            return
        seen_pairs.add(pair)
        edges.append(_random_edge(a, b))

    # Spanning structure first, so the network is guaranteed connected
    for i in range(1, len(node_ids)):
        a = node_ids[i]
        b = node_ids[random.randint(0, i - 1)]
        add_edge(a, b)

    # Extra edges for route choice / alternate paths
    for i in range(len(node_ids)):
        for j in range(i + 1, len(node_ids)):
            if random.random() < extra_edge_prob:
                add_edge(node_ids[i], node_ids[j])

    return nodes, edges


def _random_edge(a: str, b: str) -> RoadEdge:
    return RoadEdge(
        node_a=a,
        node_b=b,
        length_km=round(random.uniform(3, 40), 1),
        slope_pct=round(random.uniform(0, 25), 1),
        road_type=random.choice(ROAD_TYPES),
        max_speed_kmph=random.choice([20, 30, 40, 60]),
        hazard_score=round(random.uniform(0, 1), 2),
        intersection=random.random() < 0.15,
    )
