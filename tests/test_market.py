from popsim.market.service import MarketService


def place(owner, side, good, qty, price, ref=None):
    return {"op": "place", "owner": owner, "side": side, "good": good, "qty": qty, "price": price, "ref": ref}


def test_price_time_priority_and_maker_price():
    svc = MarketService()
    svc.submit_batch("w", "A", 0, [place(1, "sell", "food", 5, 120), place(2, "sell", "food", 5, 100),
                                   place(3, "sell", "food", 5, 100)])
    resp = svc.submit_batch("w", "A", 1, [place(9, "buy", "food", 8, 130)])
    fills = resp["results"][0]["fills"]
    # Best price first (100), earlier order first at equal price (owner 2 before 3).
    assert [(f["seller"], f["qty"], f["price"]) for f in fills] == [(2, 5, 100), (3, 3, 100)]
    assert all(f["buy_limit"] == 130 for f in fills)
    assert resp["results"][0]["remaining"] == 0
    assert resp["prices"]["food"] == 100
    assert resp["top"]["food"] == {"bid": None, "ask": 100}
    assert svc.locked("w", "A") == {"gold": 0, "food": 2 + 5}


def test_partial_fill_rests_remainder_and_cancel_reports_it():
    svc = MarketService()
    svc.submit_batch("w", "A", 0, [place(1, "sell", "wood", 2, 50)])
    r = svc.submit_batch("w", "A", 0, [place(2, "buy", "wood", 5, 60)])["results"][0]
    assert r["remaining"] == 3
    assert svc.locked("w", "A")["gold"] == 3 * 60
    c = svc.submit_batch("w", "A", 1, [{"op": "cancel", "order_id": r["order_id"], "owner": 2}])
    assert c["results"][0] == {"ok": True, "order_id": r["order_id"], "side": "buy", "good": "wood",
                               "remaining": 3, "price": 60, "op": "cancel", "ref": None}
    assert svc.locked("w", "A")["gold"] == 0


def test_batch_partial_success():
    svc = MarketService()
    resp = svc.submit_batch("w", "A", 0, [
        place(1, "buy", "food", 1, 10),
        place(1, "buy", "food", 0, 10),          # bad qty
        place(1, "hold", "food", 1, 10),         # bad side
        {"op": "cancel", "order_id": 999, "owner": 1},
        place(1, "sell", "food", 1, 0),          # bad price
    ])
    oks = [r["ok"] for r in resp["results"]]
    assert oks == [True, False, False, False, False]
    assert [r.get("error") for r in resp["results"][1:]] == ["bad_qty", "bad_side", "unknown_order", "bad_price"]


def test_cancel_requires_owner_and_cities_are_isolated():
    svc = MarketService()
    oid = svc.submit_batch("w", "A", 0, [place(1, "sell", "food", 1, 10)])["results"][0]["order_id"]
    bad = svc.submit_batch("w", "A", 0, [{"op": "cancel", "order_id": oid, "owner": 2}])
    assert bad["results"][0]["error"] == "not_owner"
    # The same order id is invisible from another city.
    other = svc.submit_batch("w", "B", 0, [{"op": "cancel", "order_id": oid, "owner": 1}])
    assert other["results"][0]["error"] == "unknown_order"
    assert svc.book("w", "B", "food") == {"bids": [], "asks": []}


def test_export_import_roundtrip():
    svc = MarketService()
    svc.submit_batch("w", "A", 0, [place(1, "sell", "food", 3, 10), place(2, "buy", "food", 1, 5)])
    state = svc.export_world("w")
    svc2 = MarketService()
    svc2.import_world("w", state)
    assert svc2.export_world("w") == state
    r = svc2.submit_batch("w", "A", 1, [place(3, "buy", "food", 3, 10)])["results"][0]
    assert r["remaining"] == 0
