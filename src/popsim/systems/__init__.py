"""System registry. The pipeline order comes from config (REQ-1A.2)."""

from __future__ import annotations

from popsim.systems.action import ActionSystem
from popsim.systems.basic import (
    AgingSystem, ConsumptionSystem, EventSystem, NeedsSystem, PhysiologySystem, RegenerationSystem,
)
from popsim.systems.decision import DecisionSystem
from popsim.systems.fiscal import FiscalSystem
from popsim.systems.market import MarketSystem


class MetricsSystem:
    name = "metrics"

    def run(self, sim) -> None:
        if (sim.tick + 1) % sim.ticks_per_day == 0:
            sim.metrics.end_of_day(sim)


REGISTRY = {
    cls.name: cls
    for cls in (EventSystem, RegenerationSystem, PhysiologySystem, NeedsSystem, ConsumptionSystem,
                DecisionSystem, ActionSystem, MarketSystem, AgingSystem, FiscalSystem, MetricsSystem)
}


def build_pipeline(names: list[str]) -> list:
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise ValueError(f"unknown systems in pipeline: {unknown}; known: {sorted(REGISTRY)}")
    return [REGISTRY[n]() for n in names]
