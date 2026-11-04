# Engineering case study (STAR)

Each workstream: **S**ituation → **T**ask → **A**ction → **R**esult. Results are what was measured in this repo;
anything not yet verified is marked.

## 1. Replace simulated data with real data
**S** The prototype's roads, slopes, hazards, stock and vehicles all came from `random.uniform`, and the frontend carried a
second copy of the engine plus an OSRM look-alike layer, silently showing synthetic data whenever the backend URL was unset.
**T** Every calculation must rest on genuine data or say clearly that it cannot.
**A** Built an ingestion pipeline: OSM roads (Overpass) → junction split → ≤ 0.5 km pieces → real SRTM elevation sampled every
~30 m → smoothed grade, ascent/descent, hillslope-based terrain hazard → provenance metadata. Rainfall from Open-Meteo at
request time. Inventory moved to a database the operator owns (CSV/API import) because no public source exists. Deleted
`simulate.py`, the 756-line TypeScript engine copy and the fake road geometry. Added an `is_fixture` flag + UI banner for
the dev fixture.
**R** Real SRTM sampling verified against published altitudes of Kathmandu, Dhulikhel, Banepa (±100 m) and ridge/valley
ordering; the fixture's elevations come from the real tile. 0 `random` calls in runtime code.
*Not yet verified:* the live OSM download (network policy of the build sandbox blocks Overpass) — one command once the host is allowed.

## 2. Make calculations correct, not just plausible
**S** The README claimed infeasible plans were filtered before Gemma, but `service.py` never checked the deadline; hazard
cost was a constant per edge (a 3 km and a 40 km road equally risky); the "fuel" figure was a unit-less proxy shown as litres.
**T** Fix the model so each number means what its label says.
**A** Deadline filter in code; hazard scaled by length and amplified by rain; direction-aware fuel in litres from base
consumption + climb energy; ingestion cuts virtual nodes in long segments; `is_drivable` validates the highway class itself.
**R** 91 tests incl. oracles: Yen's paths equal brute-force enumeration; Held-Karp equals all permutations; plan totals
(units, ⌈units/capacity⌉ vehicles, slowest-leg time) asserted; tests caught two real defects (footways admitted from saved
Overpass files; sparse ways left as multi-km edges).

## 3. Make it fast enough for real networks
**S** On a realistic graph (14,400 nodes / 28,560 edges) the first real-graph benchmark took **5.8 s** (single-source) and
**11.8 s** (infeasible) per request because Yen's algorithm ran for every warehouse.
**T** Sub-second responses without changing answers.
**A** Lazy `best_route`: Yen's first path *is* the cheapest path; run K-shortest only if it misses the deadline and a faster
path exists. Candidate set for multi-source limited to the 8 nearest warehouses.
**R** **0.06 s / 0.10 s / 0.47 s** (single / multi / infeasible) — ~100× faster; a property test checks the lazy result is
never worse than the eager definition across 30 source/target/deadline combinations.

## 4. Backend–frontend connection
**S** Frontend/back-end contract was a TypeScript port plus optional proxy; failures (Gemma down) became HTTP 503 with no answer.
**T** One source of truth, clear failure behaviour, no CORS or secret exposure.
**A** Frontend talks only to a same-origin `/api` gateway with a 5-endpoint + 1-POST allow-list; admin writes are unreachable
from the site. Typed client. If Gemma is absent/fails the recommendation (already computed in code) is returned with
`explanation_source` and `gemma_error`; Gemma's chosen index is validated; API keys never appear in errors.
**R** Gateway test: PUT/DELETE/PATCH/import/unknown → 404; full-site smoke test passes with zero browser errors.

## 5. Frontend: calmer, simpler, navigable
**S** One dense screen: dark panel, footer disclaimer, layer toggles, 12-option list, modal for explanations.
**T** Fewer decisions per screen; three obvious places to go.
**A** Three routes (Plan / Inventory / Data), a 3-input form, one result card (verdict → 4 stats → route → elevation profile
→ explanation), alternatives collapsed, hazard colouring opt-in, responsive map-over-panel layout on phones.
**R** Browser smoke test found and I fixed two real bugs (quantity `step=50` silently blocked valid input; header overflowed
390 px screens). Screens checked at 1440 px and 390 px.

## 6. Production readiness
**S** No tests, CI, containers, deploy path; tracked `__pycache__`, `.env`, a 272 MB `frontend.zip`; out-of-sync lockfile.
**T** Reproducible build and a path to AWS.
**A** Pinned requirements, Dockerfiles, compose, GitHub Actions CI (tests, lint, build, E2E, docker), OIDC deploy workflow,
CloudFormation stack (Fargate, RDS, S3, ALB, Secrets Manager), cleanup.
**R** Template passes `cfn-lint`; CI definition parses. *Not yet verified:* Docker image builds (no daemon in sandbox → CI
runs them) and the actual AWS deployment (needs your credentials).

## 7. Make the AI contribute, safely
**S** Gemma was optional decoration: it only re-worded a result the code had already fixed, and the product behaved the same without it.
**T** Give the language model work only a language model can do, without letting it touch numbers.
**A** Three features behind strict guardrails — plain-language order intake (code computes the deadline and resolves the
place; dealer confirms), verified remedies when delivery is impossible (every model proposal is re-run through the real
planner), and a risk briefing grounded in the named riskiest road stretches. Added `MARGA_REQUIRE_GEMMA` so production can
refuse to start without it. Wrote a labelled test double of the Gemma wire format to test the real HTTP client and UI.
**R** 108 backend tests (17 new for the AI paths: bad dates rejected, out-of-range/malformed/infeasible proposals dropped,
invalid choices fall back) and the browser smoke test passes with the AI flows. *Not yet verified:* real-model quality and the
model id — needs your key.
