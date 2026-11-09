"""REST market service: wire protocol, parity with the in-process engine, paired
snapshots and persistence across API restarts (REQ-1A.6)."""

import threading

import pytest

from popsim.io.snapshot import load_snapshot, save_snapshot
from popsim.market.client import HttpMarketClient, LocalMarketClient, MarketAPIError
from popsim.market.server import make_server
from popsim.world import Simulation


@pytest.fixture
def api(tmp_path):
    servers = []

    def start(db=None):
        server = make_server("127.0.0.1", 0, str(db or tmp_path / "api.sqlite"))
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        servers.append(server)
        return server, HttpMarketClient(f"http://127.0.0.1:{server.server_address[1]}")

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def test_routes_and_batches(api):
    _, client = api()
    assert client.health() == {"ok": True}
    resp = client.submit_batch("w1", "A", 0, [
        {"op": "place", "owner": 1, "side": "sell", "good": "food", "qty": 2, "price": 100},
        {"op": "place", "owner": 2, "side": "buy", "good": "food", "qty": 3, "price": 110},
        {"op": "place", "owner": 2, "side": "buy", "good": "food", "qty": -1, "price": 110},
    ])
    assert [r["ok"] for r in resp["results"]] == [True, True, False]
    assert resp["results"][1]["fills"][0]["price"] == 100
    assert client.prices("w1", "A") == {"food": 100}
    assert client.book("w1", "A", "food") == {"bids": [[110, 1]], "asks": []}
    assert client.prices("w1", "B") == {}  # other cities see nothing
    with pytest.raises(MarketAPIError):
        client._call("POST", "/w1/A/orders/batch", {"orders": "nope"})


def test_http_market_matches_in_process_market(api, small_cfg):
    _, client = api()
    local = Simulation(small_cfg, market=LocalMarketClient(json_roundtrip=True))
    remote = Simulation(small_cfg, market=client)
    local.run(days=2)
    remote.run(days=2)
    assert remote.metrics.tick_prices == local.metrics.tick_prices
    assert all(v["ok"] for v in remote.escrow_matches_market().values())


def test_paired_snapshots_survive_api_restart(api, small_cfg, tmp_path):
    db = tmp_path / "persist.sqlite"
    server, client = api(db)
    sim = Simulation(small_cfg, market=client)
    sim.run(days=1)
    save_snapshot(sim, tmp_path / "w.pkl")  # API stores the market half in SQLite
    market_at_save = client.export_world(sim.world_name)
    sim.run(days=1)  # live API state moves on, unsaved

    server.shutdown()
    server.server_close()
    _, client2 = api(db)  # restart: latest snapshot per world is loaded
    assert client2.export_world(sim.world_name) == market_at_save

    resumed = load_snapshot(tmp_path / "w.pkl", market=client2)
    resumed.run(days=1)
    assert resumed.ledger.verify()["ok"]
    assert all(v["ok"] for v in resumed.escrow_matches_market().values())
    # Same trajectory as the original run's second day.
    n = 24 * len(sim.city_ids) * len(sim.goods)
    assert resumed.metrics.tick_prices[-n:] == sim.metrics.tick_prices[-n:]
