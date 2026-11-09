"""Acceptance checks 4 and 5 at unit level, plus the urgency curve."""

from popsim.components import Health, Stamina
from popsim.config import load_config
from popsim.systems.agent import stamina_per_unit, urgency
from popsim.systems.basic import daily_stamina_update, sickness_probability

CFG = load_config()
SCFG = CFG["physiology"]["stamina"]
HCFG = CFG["physiology"]["health"]


def test_skipping_sleep_lowers_daily_max_and_raises_sickness():
    rested = Stamina(current=100, daily_max=100, absolute_max=100)
    tired = Stamina(current=100, daily_max=100, absolute_max=100)
    hl = Health(immune_strength=0.6, base_immune=0.6)
    for _ in range(3):
        rested.sleep_hours_today = 8
        tired.sleep_hours_today = 2
        daily_stamina_update(rested, SCFG)
        daily_stamina_update(tired, SCFG)
    assert rested.daily_max == 100
    assert tired.daily_max < 100
    assert sickness_probability(tired, hl, False, HCFG, 1) > sickness_probability(rested, hl, False, HCFG, 1)


def test_spring_elasticity_recovers_slowly():
    st = Stamina(current=50, daily_max=50, absolute_max=100)
    gains = []
    for _ in range(4):
        before = st.daily_max
        st.sleep_hours_today = 8
        daily_stamina_update(st, SCFG)
        gains.append(st.daily_max - before)
    assert gains == sorted(gains) and gains[0] < gains[-1]  # accelerates with the streak
    assert st.daily_max < 100  # but does not snap back in a few days


def test_higher_skill_costs_less_stamina_per_unit():
    assert stamina_per_unit(5.0, 2.0) < stamina_per_unit(5.0, 1.0) < stamina_per_unit(5.0, 0.5)


def test_urgency_is_zero_above_comfort_one_at_critical_and_steep_below():
    assert urgency(0.7, 0.6, 0.15, 2) == 0
    assert abs(urgency(0.15, 0.6, 0.15, 2) - 1.0) < 1e-9
    assert urgency(0.0, 0.6, 0.15, 2) > 1.5
    # Nonlinear: the second half of the drop adds more urgency than the first.
    a, b, c = urgency(0.6, 0.6, 0.15, 2), urgency(0.375, 0.6, 0.15, 2), urgency(0.15, 0.6, 0.15, 2)
    assert (c - b) > (b - a)
