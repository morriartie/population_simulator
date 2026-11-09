from dataclasses import dataclass

import pytest

from popsim.ecs import World
from popsim.ledger import InsufficientFunds, Ledger, LedgerError


@dataclass
class Pos:
    x: int


@dataclass
class Vel:
    v: int


def test_query_returns_sorted_entities_with_all_components():
    w = World()
    a = w.create(Pos(1), Vel(1))
    b = w.create(Pos(2))
    c = w.create(Vel(3), Pos(3))
    assert [e for e, _ in w.query(Pos, Vel)] == [a, c]
    assert [e for e, _ in w.query(Pos)] == [a, b, c]
    w.destroy(a)
    assert [e for e, _ in w.query(Pos, Vel)] == [c]
    assert not w.alive(a) and w.get(a, Pos) is None


def test_ledger_transfers_and_invariants():
    led = Ledger()
    led.transfer("authority", 1, "gold", 500, "genesis")
    led.transfer("nature", 1, "food", 3, "genesis")
    led.transfer(1, 2, "gold", 200, "trade")
    assert led.balance(1, "gold") == 300 and led.balance(2, "gold") == 200
    assert led.supply("gold") == 500
    with pytest.raises(InsufficientFunds):
        led.transfer(2, 1, "gold", 201)
    assert not led.try_transfer(2, 1, "gold", 201)
    with pytest.raises(LedgerError):
        led.transfer(1, 2, "gold", 1.5)
    with pytest.raises(LedgerError):
        led.transfer(1, 2, "gold", -1)
    rep = led.verify()
    assert rep["ok"] and rep["log_replay_matches"] and rep["totals_zero"]


def test_ledger_detects_tampering():
    led = Ledger()
    led.transfer("authority", 1, "gold", 100)
    led.balances[1]["gold"] = 150  # value created outside the ledger API
    rep = led.verify()
    assert not rep["ok"] and not rep["totals_zero"] and not rep["log_replay_matches"]
