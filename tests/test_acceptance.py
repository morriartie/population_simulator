"""Scenario acceptance checks (REQ-5.2, REQ-5.3, section 6). Each compares a run
with its counterfactual under the same seed, and checks direction, not exact
numbers. Run time: ~30 s."""

import statistics

import pytest

from popsim.world import Simulation


def mean_price(sim, city, good, day_from, day_to):
    vals = [r["vwap"] for r in sim.metrics.daily_prices
            if r["city"] == city and r["good"] == good and day_from <= r["day"] < day_to and r["vwap"]]
    return statistics.fmean(vals) if vals else None


@pytest.mark.slow
def test_stimulus_raises_prices(scenario):
    treated = Simulation(scenario("inflation"))
    control = Simulation(scenario("inflation_control"))
    treated.run(days=40)
    control.run(days=40)
    # Identical until the stimulus on day 20 ...
    assert mean_price(treated, "A", "food", 0, 20) == mean_price(control, "A", "food", 0, 20)
    # ... then a higher average price level afterwards.
    t = statistics.fmean(mean_price(treated, "A", g, 22, 40) for g in ("food", "wood"))
    c = statistics.fmean(mean_price(control, "A", g, 22, 40) for g in ("food", "wood"))
    assert t > 1.2 * c, (t, c)
    assert treated.ledger.verify()["ok"]


@pytest.mark.slow
def test_road_narrows_wood_price_gap(scenario):
    road = Simulation(scenario("arbitrage"))
    isolated = Simulation(scenario("baseline"))
    road.run(days=50)
    isolated.run(days=50)

    def gap(sim, a, b):
        pa, pb = mean_price(sim, "A", "wood", a, b), mean_price(sim, "B", "wood", a, b)
        assert pa and pb
        return pb - pa

    shipped = sum(f["qty"] for f in road.metrics.flows if f["good"] == "wood" and f["to"] == "B")
    assert shipped > 100
    # Gap right after the road opens vs. a month later.
    assert gap(road, 35, 50) < 0.6 * gap(road, 15, 22)
    # And B's wood is cheaper with the road than in the isolated world.
    last_isolated = [r["last"] for r in isolated.metrics.daily_prices
                     if r["city"] == "B" and r["good"] == "wood" and r["last"]][-1]
    assert mean_price(road, "B", "wood", 35, 50) < last_isolated
    assert road.ledger.verify()["ok"]
