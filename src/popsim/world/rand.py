"""Seeded sampling helpers (REQ-4.2.4: Gaussian around defaults, clamped)."""

from __future__ import annotations

import math
import random


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def gauss_spec(rng: random.Random, spec: dict) -> float:
    v = rng.gauss(spec["mean"], spec.get("sd", 0.0))
    return clamp(v, spec.get("min", -math.inf), spec.get("max", math.inf))


def lognormal_skill(rng: random.Random, spec: dict) -> float:
    return clamp(math.exp(rng.gauss(0.0, spec["sigma"])), spec["min"], spec["max"])
