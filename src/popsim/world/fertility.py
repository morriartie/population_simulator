"""The fertility field ``f(x, y)`` in [0, 1] (the farming policy, REQ-5A.2).

A pure function of the scenario's ``fertility`` settings and a point, so it needs no state, no random
stream and no simulation: the same seed gives the same land everywhere. It is smooth value noise
(several octaves) plus optional hand-placed ``blobs`` of good land. Whether a point may be planted also
depends on the world (outside every city, away from other fields); that part lives in the policy.
"""

from __future__ import annotations

import math

DEFAULTS = {"seed": 7, "scale": 14.0, "octaves": 3, "persistence": 0.5, "base": 1.0, "blobs": []}


def _lattice(seed: int, ix: int, iy: int) -> float:
    """A repeatable pseudo-random value in [0, 1) for one lattice corner."""
    n = (ix * 374761393 + iy * 668265263 + seed * 2147483647) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    n ^= n >> 16
    return n / 4294967296.0


def _smooth(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def value_noise(seed: int, x: float, y: float) -> float:
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = _smooth(x - ix), _smooth(y - iy)
    a, b = _lattice(seed, ix, iy), _lattice(seed, ix + 1, iy)
    c, d = _lattice(seed, ix, iy + 1), _lattice(seed, ix + 1, iy + 1)
    top, bottom = a + (b - a) * fx, c + (d - c) * fx
    return top + (bottom - top) * fy


def settings(cfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    out.update(cfg or {})
    return out


def fertility(cfg: dict | None, x: float, y: float) -> float:
    """Fertility at ``(x, y)`` for the settings ``cfg`` (missing keys take ``DEFAULTS``)."""
    s = settings(cfg)
    total, amp, norm, freq = 0.0, 1.0, 0.0, 1.0 / max(1e-6, float(s["scale"]))
    for o in range(max(1, int(s["octaves"]))):
        total += amp * value_noise(int(s["seed"]) + 101 * o, x * freq, y * freq)
        norm += amp
        amp *= float(s["persistence"])
        freq *= 2.0
    f = float(s["base"]) * total / norm
    for b in s["blobs"] or []:
        bx, by = b["at"]
        r = max(1e-6, float(b.get("radius", 5.0)))
        f += float(b.get("strength", 0.5)) * math.exp(-((x - bx) ** 2 + (y - by) ** 2) / (2 * r * r))
    return max(0.0, min(1.0, f))


def grid(cfg: dict | None, x0: float, y0: float, x1: float, y1: float, cell: float) -> dict:
    """The field sampled on a regular grid (row 0 is ``y0``), for drawing."""
    nx = max(1, int(math.ceil((x1 - x0) / cell)))
    ny = max(1, int(math.ceil((y1 - y0) / cell)))
    rows = [[round(fertility(cfg, x0 + (i + 0.5) * cell, y0 + (j + 0.5) * cell), 3) for i in range(nx)]
            for j in range(ny)]
    return {"x0": x0, "y0": y0, "cell": cell, "nx": nx, "ny": ny, "values": rows}
