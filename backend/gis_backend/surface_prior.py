"""
Empirical surface prior for roads whose surface is not tagged.

In the Kathmandu and Kavre data about three quarters of road pieces have no surface tag. Guessing from hand-picked
class defaults (the earlier approach) was right on 78% of held-out tagged roads. Using the share of paved, gravel and
rough surfaces among the tagged roads of the same class in the same area is right on 86%, and it also gives a
probability, which matters for a class like "unclassified" that is nearly a coin flip (41% paved, 51% rough).

Costs, times and fuel for an untagged road use these probabilities as expectations (see ``edge_weight``).

Caveat: mappers may tag some surfaces more often than others, which would bias the prior. The holdout score
measures prediction on tagged roads only, so it cannot detect that. Replace the prior with a survey where you have one.
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

SURFACES = ("paved", "gravel", "rough")
Row = Tuple[str, str, float]  # (highway class, surface, weight such as length in km)


def fit_priors(rows: Iterable[Row], alpha: float = 1.0) -> Tuple[Dict[str, Tuple[float, float, float]], Tuple[float, float, float]]:
    by: Dict[str, Counter] = defaultdict(Counter)
    total: Counter = Counter()
    for hw, s, w in rows:
        by[hw][s] += w
        total[s] += w

    def norm(c: Counter) -> Tuple[float, float, float]:
        t = sum(c[s] + alpha for s in SURFACES)
        return tuple((c[s] + alpha) / t for s in SURFACES)  # type: ignore[return-value]

    return {hw: norm(c) for hw, c in by.items()}, norm(total)


def holdout_accuracy(rows: List[Row], static_default: Dict[str, str], seeds: int = 20, frac: float = 0.3) -> dict:
    """Mean accuracy over random train/test splits of the tagged roads (counts, not lengths)."""
    if len(rows) < 50:
        return {"n_tagged": len(rows), "prior_accuracy": None, "static_default_accuracy": None}
    prior_acc, default_acc = [], []
    for seed in range(seeds):
        rnd = random.Random(seed)
        idx = list(range(len(rows)))
        rnd.shuffle(idx)
        cut = int(len(idx) * (1 - frac))
        train, test = [rows[i] for i in idx[:cut]], [rows[i] for i in idx[cut:]]
        priors, glob = fit_priors(train)
        hit = lambda p, s: SURFACES[max(range(3), key=lambda k: p[k])] == s  # noqa: E731
        prior_acc.append(sum(hit(priors.get(hw, glob), s) for hw, s, _ in test) / len(test))
        default_acc.append(sum(static_default.get(hw, "gravel") == s for hw, s, _ in test) / len(test))
    return {"n_tagged": len(rows), "prior_accuracy": round(sum(prior_acc) / seeds, 3),
            "static_default_accuracy": round(sum(default_acc) / seeds, 3)}
