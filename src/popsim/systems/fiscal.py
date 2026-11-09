"""Taxes and public spending (REQ-5.5).

Every city has a treasury (the city entity's gold account). Policy is fixed per scenario, with
no feedback from NPCs or metrics, and each city collects and spends locally:

* income tax: a share of wages and of sales proceeds, paid to the treasury of the city where the
  money was earned;
* sales tax: an optional share of each transaction's value, paid by the buyer to the city's treasury;
* public jobs: workplaces pay a wage from the city treasury for each hour worked;
* support: a daily payment from the treasury to residents whose survival needs are low.

All of it moves gold with ledger transfers, so conservation (REQ-5.1) holds.
"""

from __future__ import annotations

from popsim.components import Action, Location, NPC, Needs, Record, Workplace
from popsim.ledger import GOLD

SURVIVAL_NEEDS = ("food", "rest", "shelter")
DEFAULT = {"income_tax": 0.0, "sales_tax": 0.0, "support": {"amount": 0.0, "hour": 8},
           "public_jobs": {"stamina_per_hour": 3.5, "slots": 3}}


def policy(sim, city: int) -> dict:
    """The fiscal policy of one city: the scenario's global ``fiscal`` with the city's own overrides."""
    cache = sim.__dict__.setdefault("_policy", {})
    pol = cache.get(city)
    if pol is None:
        base = sim.cfg.get("fiscal") or DEFAULT
        pol = {"income_tax": base.get("income_tax", 0.0), "sales_tax": base.get("sales_tax", 0.0),
               "support": dict(base.get("support") or DEFAULT["support"]),
               "public_jobs": dict(base.get("public_jobs") or DEFAULT["public_jobs"])}
        name = sim.city_name(city)
        own = next((c.get("fiscal") for c in sim.cfg["cities"] if c["name"] == name), None) or {}
        for k in ("income_tax", "sales_tax"):
            if k in own:
                pol[k] = own[k]
        pol["support"].update(own.get("support") or {})
        cache[city] = pol
    return pol


def _note(sim, city: int, kind: str, cents: int) -> None:
    sim.metrics.record_treasury(sim.city_name(city), kind, cents)


def pay_income_tax(sim, eid: int, city: int, gross: int, reason: str) -> int:
    """Tax ``gross`` cents of income just received by ``eid`` in ``city``. Returns the tax paid."""
    rate = policy(sim, city)["income_tax"]
    tax = min(int(round(gross * rate)), sim.gold(eid)) if rate else 0
    if tax > 0:
        sim.ledger.transfer(eid, city, GOLD, tax, reason=reason)
        _note(sim, city, "tax", tax)
        rec = sim.world.get(eid, Record)
        if rec is not None:
            rec.taxes_today += tax
    return tax


def pay_sales_tax(sim, buyer: int, city: int, value: int) -> int:
    """The buyer's share of a transaction's value, as far as the buyer can afford it."""
    rate = policy(sim, city)["sales_tax"]
    tax = min(int(round(value * rate)), sim.gold(buyer)) if rate else 0
    if tax > 0:
        sim.ledger.transfer(buyer, city, GOLD, tax, reason="sales_tax")
        _note(sim, city, "tax", tax)
        rec = sim.world.get(buyer, Record)
        if rec is not None:
            rec.taxes_today += tax
    return tax


# ----------------------------------------------------------------- public jobs


def workers_at(sim) -> dict[int, int]:
    """How many people are working at each workplace right now."""
    out: dict[int, int] = {}
    for _, (act,) in sim.world.query(Action):
        if act.kind == "work" and act.target is not None:
            out[act.target] = out.get(act.target, 0) + 1
        elif act.kind == "walk" and act.then and act.then[0] == "work":
            out[act.then[1]] = out.get(act.then[1], 0) + 1  # on their way: the slot is taken
    return out


def slots_of(sim, w: Workplace) -> int:
    return w.slots if w.slots else policy(sim, w.city)["public_jobs"]["slots"]


def net_wage_per_hour(sim, w: Workplace) -> float:
    """What a worker keeps per hour after income tax, in cents."""
    return w.wage * (1.0 - policy(sim, w.city)["income_tax"])


def pay_wage(sim, eid: int, wid: int) -> int:
    """Pay one tick of work at workplace ``wid`` from its city's treasury. Returns the net cents
    the worker received, or -1 when the treasury cannot pay (the job closes for now)."""
    w = sim.world.get(wid, Workplace)
    gross = int(round(w.wage * sim.h))
    if gross <= 0:
        return 0
    if not sim.ledger.try_transfer(w.city, eid, GOLD, gross, reason="wage"):
        return -1
    tax = pay_income_tax(sim, eid, w.city, gross, "income_tax")
    sim.metrics.record_treasury(sim.city_name(w.city), "wages", gross)
    return gross - tax


# --------------------------------------------------------------------- support


class FiscalSystem:
    """Daily support payment: residents with a survival need at or below its urgent level share
    the support the treasury can afford (each gets at most the configured amount)."""

    name = "fiscal"

    def run(self, sim) -> None:
        if sim.tick == 0:
            return
        for name, cid in sorted(sim.city_ids.items(), key=lambda kv: kv[1]):
            sup = policy(sim, cid)["support"]
            amount = int(round(float(sup.get("amount", 0.0)) * sim.unit))
            if amount <= 0 or not sim.at_hour(float(sup.get("hour", 8))):
                continue
            needy = []
            for eid, (_, loc, needs) in sim.world.query(NPC, Location, Needs):
                if loc.city == cid and any(needs.levels[n] <= needs.urgent[n] for n in SURVIVAL_NEEDS):
                    needy.append(eid)
            if not needy:
                continue
            each = min(amount, sim.gold(cid) // len(needy))  # the treasury is finite
            for eid in needy:
                if each > 0:
                    sim.ledger.transfer(cid, eid, GOLD, each, reason="support")
                    rec = sim.world.get(eid, Record)
                    if rec is not None:
                        rec.support_today += each
            if each > 0:
                _note(sim, cid, "support", each * len(needy))
