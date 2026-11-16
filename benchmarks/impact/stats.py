"""Interval estimates used by the report. Pure functions, standard library only."""
from __future__ import annotations

import math
import random


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95 % Wilson score interval of a proportion."""
    if total == 0:
        return 0.0, 1.0
    p = successes / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    denominator = 1 + z * z / total
    return max(0.0, (centre - spread) / denominator), min(1.0, (centre + spread) / denominator)


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    position = share * (len(ordered) - 1)
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap_mean(values: list[float], rounds: int = 10000, seed: int = 1) -> tuple[float, float, float]:
    """Mean and its 95 % percentile bootstrap interval."""
    rng = random.Random(seed)
    n = len(values)
    means = [sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(rounds)]
    return sum(values) / n, percentile(means, 0.025), percentile(means, 0.975)


def bootstrap_ratio(numerators: list[float], denominators: list[float], rounds: int = 10000, seed: int = 1) -> tuple[float, float, float]:
    """sum(numerators) / sum(denominators) - 1 over paired items, with a 95 % paired bootstrap interval."""
    rng = random.Random(seed)
    n = len(numerators)
    draws = []
    for _ in range(rounds):
        picks = [rng.randrange(n) for _ in range(n)]
        draws.append(sum(numerators[i] for i in picks) / sum(denominators[i] for i in picks) - 1)
    return sum(numerators) / sum(denominators) - 1, percentile(draws, 0.025), percentile(draws, 0.975)


def sign_test(first_only: int, second_only: int) -> float:
    """Two-sided exact binomial test on the discordant pairs (McNemar's exact test)."""
    n = first_only + second_only
    if n == 0:
        return 1.0
    k = min(first_only, second_only)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)
