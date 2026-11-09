"""Central ledger (REQ-1A.5, REQ-5.1).

Every movement of gold or goods is a ``transfer`` between two accounts. Accounts
are entity IDs (NPCs, cities/treasuries, resource nodes) or named system accounts:

* ``"authority"`` – the central authority, the only account that can create gold.
* ``"nature"``    – source of regenerated resources and sink for consumption.
* ``"escrow:<city>"`` – funds locked by open market orders in that city.

Only the two source accounts may go negative. Starting from all-zero balances,
the sum of every asset over every account is therefore always exactly zero, and
the circulating supply of an asset is minus the balance of its source.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Hashable

SOURCES = ("authority", "nature")
GOLD = "gold"


class LedgerError(Exception):
    pass


class InsufficientFunds(LedgerError):
    pass


def escrow_account(city_id: int) -> str:
    return f"escrow:{city_id}"


class Ledger:
    def __init__(self, keep_log: bool = True) -> None:
        self.balances: dict[Hashable, dict[str, int]] = defaultdict(dict)
        self.keep_log = keep_log
        self.log: list[tuple[int, Hashable, Hashable, str, int, str]] = []
        self.tick = 0
        self.transfer_count = 0

    # -- reads ------------------------------------------------------------
    def balance(self, account: Hashable, asset: str) -> int:
        acct = self.balances.get(account)
        return acct.get(asset, 0) if acct else 0

    def holdings(self, account: Hashable) -> dict[str, int]:
        return {a: v for a, v in self.balances.get(account, {}).items() if v}

    def assets(self) -> list[str]:
        found: dict[str, None] = {}
        for acct in self.balances.values():
            for asset in acct:
                found[asset] = None
        return sorted(found)

    def supply(self, asset: str) -> int:
        """Units held by all non-source accounts."""
        return sum(
            bal.get(asset, 0) for acct, bal in self.balances.items() if acct not in SOURCES
        )

    # -- writes -----------------------------------------------------------
    def transfer(self, src: Hashable, dst: Hashable, asset: str, amount: int, reason: str = "") -> None:
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise LedgerError(f"amount must be int, got {amount!r}")
        if amount < 0:
            raise LedgerError("amount must be non-negative")
        if amount == 0:
            return
        if src == dst:
            raise LedgerError("src and dst are the same account")
        src_bal = self.balance(src, asset)
        if src not in SOURCES and src_bal < amount:
            raise InsufficientFunds(f"{src} has {src_bal} {asset}, needs {amount} ({reason})")
        self.balances[src][asset] = src_bal - amount
        self.balances[dst][asset] = self.balance(dst, asset) + amount
        self.transfer_count += 1
        if self.keep_log:
            self.log.append((self.tick, src, dst, asset, amount, reason))

    def try_transfer(self, src: Hashable, dst: Hashable, asset: str, amount: int, reason: str = "") -> bool:
        try:
            self.transfer(src, dst, asset, amount, reason)
            return True
        except InsufficientFunds:
            return False

    def transfer_all(self, src: Hashable, dst: Hashable, reason: str = "") -> None:
        for asset, amount in sorted(self.holdings(src).items()):
            if amount > 0:
                self.transfer(src, dst, asset, amount, reason)

    # -- invariants -------------------------------------------------------
    def check_totals(self) -> dict[str, int]:
        """Return assets whose grand total is not zero (should always be empty)."""
        bad = {}
        for asset in self.assets():
            total = sum(b.get(asset, 0) for b in self.balances.values())
            if total != 0:
                bad[asset] = total
        return bad

    def negative_accounts(self) -> list[tuple[Hashable, str, int]]:
        out = []
        for acct, bal in self.balances.items():
            if acct in SOURCES:
                continue
            for asset, v in bal.items():
                if v < 0:
                    out.append((acct, asset, v))
        return out

    def verify(self) -> dict:
        """Replay the log from zero and compare with live balances."""
        report = {
            "totals_zero": not self.check_totals(),
            "no_negative_balances": not self.negative_accounts(),
            "log_replay_matches": None,
            "transfers": self.transfer_count,
        }
        if self.keep_log:
            replay: dict[Hashable, dict[str, int]] = defaultdict(dict)
            for _, src, dst, asset, amount, _ in self.log:
                replay[src][asset] = replay[src].get(asset, 0) - amount
                replay[dst][asset] = replay[dst].get(asset, 0) + amount
            live = {a: {k: v for k, v in b.items() if v} for a, b in self.balances.items()}
            rep = {a: {k: v for k, v in b.items() if v} for a, b in replay.items()}
            live = {a: b for a, b in live.items() if b}
            rep = {a: b for a, b in rep.items() if b}
            report["log_replay_matches"] = live == rep
        report["ok"] = all(v is not False for v in report.values())
        return report

    def flows(self, reason_prefix: str = "") -> dict[tuple[str, str], int]:
        """Sum of transfers by (reason, asset), optionally filtered by reason prefix."""
        out: dict[tuple[str, str], int] = defaultdict(int)
        for _, _, _, asset, amount, reason in self.log:
            if reason.startswith(reason_prefix):
                out[(reason, asset)] += amount
        return dict(out)
