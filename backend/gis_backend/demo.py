"""
Offline demo: simulates a network and runs a couple of example requests
through the full workflow, printing results. Useful for verifying the
routing/feasibility pipeline works before wiring up the frontend.

By default this uses the real reasoner resolution (get_reasoner()), which
means: if GEMMA_API_KEY isn't set, you'll see status="reasoning_unavailable"
for every request -- that's intentional, not a bug. All routing/feasibility
math still ran and is shown in candidate_plans; only the Gemma selection
step is missing.

Run with: python -m gis_backend.demo
Run with the offline rule-based stand-in instead (no API key needed, but
NOT real Gemma reasoning -- clearly labeled as such): python -m gis_backend.demo --mock
"""
import sys

from .gemma_reasoner import MockGemmaReasoner
from .models import DeliveryRequest
from .service import process_request
from .simulate import simulate_network


def run(destination: str, quantity: int, deadline_hours: float, nodes, edges, reasoner=None):
    request = DeliveryRequest(destination=destination, quantity=quantity, deadline_hours=deadline_hours)
    print(f"\nRequest: {quantity} units to {destination} within {deadline_hours}h")
    result = process_request(nodes, edges, request, reasoner=reasoner)
    print("Status:", result["status"])
    print("Explanation:", result["explanation"])
    if result.get("gemma_error"):
        print("Gemma error detail:", result["gemma_error"])
    if result.get("chosen_plan"):
        print("Chosen plan:", result["chosen_plan"])
    if result.get("candidate_plans"):
        print(f"Candidate plans computed (deterministic, unselected): {len(result['candidate_plans'])}")
    if result.get("rejected_plans"):
        print(f"Rejected plans considered: {len(result['rejected_plans'])}")


def main():
    use_mock = "--mock" in sys.argv
    reasoner = MockGemmaReasoner() if use_mock else None  # None -> get_reasoner() -> live Gemma

    if use_mock:
        print("Running with MockGemmaReasoner (offline rule-based stand-in, NOT real Gemma reasoning).\n")
    else:
        print("Running with the live Gemma reasoner. Without GEMMA_API_KEY set, requests will "
              "report status='reasoning_unavailable' -- that's the intended honest behavior, "
              "not a bug. Pass --mock to see the offline rule-based stand-in instead.\n")

    nodes, edges = simulate_network(num_nodes=12, seed=7)

    print("Simulated network:")
    for nid, n in nodes.items():
        print(f"  {nid}: stock={n.units_in_stock:4d}  vehicles={n.vehicles_available}  "
              f"max_deliverable={n.max_deliverable_units}")

    # 1) Modest order, generous deadline -- likely single-source feasible.
    run("W0", 150, 8.0, nodes, edges, reasoner=reasoner)

    # 2) Larger order that likely needs multiple sources.
    run("W1", 1000, 10.0, nodes, edges, reasoner=reasoner)

    # 3) Same large order with a very tight deadline -- likely infeasible.
    run("W2", 1000, 1.0, nodes, edges, reasoner=reasoner)


if __name__ == "__main__":
    main()
