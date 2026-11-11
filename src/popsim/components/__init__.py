"""Pure-data components (REQ-1A.9). Entities reference each other by integer ID only.

Gold and goods balances (the spec's Wallet and Inventory) are held by the Ledger,
keyed by entity ID (REQ-1A.5), so there is exactly one source of truth for every
unit of value. The ``Account`` marker says an entity can hold balances.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- biology (2.1)


@dataclass
class Stamina:
    current: float
    daily_max: float
    absolute_max: float
    sleep_hours_today: float = 0.0  # since the last daily recalculation (at noon)
    good_rest_streak: int = 0


@dataclass
class Metabolism:
    satiety: float  # 0..1, same value as Needs.levels["food"]
    base_burn: float  # satiety lost per hour at rest
    burn_multiplier: float = 1.0  # set each tick from the current activity


@dataclass
class Health:
    immune_strength: float
    base_immune: float
    sickness_probability: float = 0.0  # per tick, recomputed each tick
    sick: bool = False


@dataclass
class Demographics:
    age_days: float
    gender: str
    life_expectancy_days: float


# --------------------------------------------------------------- economy (2.2)


@dataclass
class Account:
    kind: str  # "npc" | "city" | "node"


@dataclass
class Skills:
    levels: dict[str, float]


@dataclass
class Traits:
    risk: float
    urgency_tolerance: float
    thrift: float
    luxury: float
    forecast_noise: float
    reactivity: float  # scales the EMA alpha: >1 over-reacts, <1 under-reacts
    need_weights: dict[str, float]
    job_preference: dict[str, float]
    stock_days: float


@dataclass
class OpenOrder:
    order_id: int
    city: int
    side: str  # "buy" | "sell"
    good: str
    qty: int  # remaining
    price: int  # limit, in cents
    placed_tick: int


@dataclass
class Orders:
    open: list[OpenOrder] = field(default_factory=list)
    bid_premium: dict[str, float] = field(default_factory=dict)
    ask_discount: dict[str, float] = field(default_factory=dict)


@dataclass
class PriceMemory:
    # (city_id, good) -> [ema_price_cents, last_seen_tick]
    ema: dict[tuple[int, str], list[float]] = field(default_factory=dict)


# ----------------------------------------------------------------- world (3.x)


@dataclass
class City:
    name: str


@dataclass
class Location:
    city: int | None  # None while travelling


@dataclass
class Home:
    city: int
    house: int | None = None  # a House entity (REQ-3A.8); None = no house, sleep where you stand


@dataclass
class Yield:
    """Resource-node output. The current amount is the node's ledger balance."""

    good: str
    max_amount: int
    base_rate: float  # units per hour at skill 1.0
    stamina_cost: float  # stamina per unit at skill 1.0


@dataclass
class RegenerationRate:
    per_hour: float
    carry: float = 0.0


@dataclass
class Road:
    a: int
    b: int
    hours: float
    stamina_cost: float
    length: float | None = None  # distance units; None in scenarios without coordinates (REQ-3A.2)


@dataclass
class Travel:
    origin: int
    dest: int
    ticks_left: int
    stamina_per_tick: float
    total_ticks: int = 0  # for placing the traveller along the road
    from_pos: tuple[float, float] | None = None  # where the leg starts when it is not ``origin`` (a node, REQ-3A.11)


@dataclass
class Branch:
    """A resource node on the overworld (REQ-3A.11): the main road (cities ``a`` and ``b``) it hangs off, how far
    along it the junction lies (``t``, 0 at ``a``), and the branch road from the junction to the node."""

    a: int
    b: int
    t: float
    length: float
    hours: float
    stamina_cost: float
    jx: float = 0.0  # the junction, for drawing
    jy: float = 0.0


@dataclass
class Trip:
    """A gathering trip out to an overworld node (REQ-3A.11): the city it started from and the node."""

    origin: int
    node: int


# --------------------------------------------------------------------- AI (4.x)


@dataclass
class NPC:
    name: str


@dataclass
class Needs:
    levels: dict[str, float]
    comfort: dict[str, float]
    urgent: dict[str, float]
    critical: dict[str, float]
    urgency: dict[str, float] = field(default_factory=dict)
    zero_hours: dict[str, float] = field(default_factory=dict)


@dataclass
class Action:
    kind: str = "idle"  # idle | sleep | gather | work | travel | trade | walk
    target: int | None = None
    ticks_left: int = 0
    carry: float = 0.0
    # walk (REQ-3A.5): straight line from ``walk_from`` to ``walk_to``, then ``then`` starts
    walk_from: tuple[float, float] | None = None
    walk_to: tuple[float, float] | None = None
    walk_total: int = 0
    walk_stamina: float = 0.0  # stamina per tick
    then: tuple | None = None  # ("gather", node, ticks) | ("sleep", None, ticks)


@dataclass
class Position:
    """Where an entity is on the plane (REQ-3A.1), in distance units. Entities of
    scenarios without coordinates all sit at (0, 0), so nothing is ever far away."""
    x: float = 0.0
    y: float = 0.0


@dataclass
class Area:
    radius: float  # a city's extent around its Position


@dataclass
class House:
    city: int
    owner: int | None = None
    occupant: int | None = None


@dataclass
class Workplace:
    """A non-gathering job site (public jobs, contracts). Its Position is where the work is done."""
    city: int
    task: str
    wage: int = 0  # cents per hour, paid by the city's treasury (REQ-5.5)
    slots: int = 3  # how many people can work here at once
    stamina_per_hour: float = 3.5


@dataclass
class Role:
    kind: str  # "worker" | "merchant"


@dataclass
class MerchantState:
    mode: str = "idle"  # idle | buying | traveling | selling
    good: str | None = None
    dest: int | None = None
    qty: int = 0
    wait: int = 0
    max_buy_price: int = 0
    visited: list[int] = field(default_factory=list)


@dataclass
class Record:
    """Bounded per-NPC history for the dashboard inspector (REQ-1C.6).

    Pure data. Filled by ``popsim.records`` from state the systems already
    computed; it never feeds back into a decision and never touches the RNG.
    """

    size: int = 500
    # (tick, city, good, side, qty, price_cents, counterparty_eid)
    fills: deque = field(default_factory=deque)
    # {"kind", "target", "label", "city", "start", "end", "reason"}
    actions: deque = field(default_factory=deque)
    # {"node", "good", "city", "start", "end", "units", "value"} (value in cents, at the NPC's own price)
    tasks: deque = field(default_factory=deque)
    # {"day", "spent", "earned", "gold"} in cents
    daily: deque = field(default_factory=deque)
    cur_action: dict[str, Any] | None = None
    cur_task: dict[str, Any] | None = None
    reason: dict[str, Any] | None = None  # why the latest decision was made
    spent_today: int = 0
    earned_today: int = 0
    pending_units: int = 0
    pending_value: float = 0.0
    taxes_today: int = 0
    support_today: int = 0
    walked: dict[str, Any] | None = None  # a walk tick that may be over before the tick ends


def new_record(size: int) -> Record:
    size = max(1, int(size))
    return Record(size=size, fills=deque(maxlen=size), actions=deque(maxlen=size),
                  tasks=deque(maxlen=size), daily=deque(maxlen=size))


# ---------------------------------------------------------------- player (REQ-1C.9)


@dataclass
class PlayerOrder:
    """One order of the player. It is queued first and sent to the market at the next tick."""
    id: int
    city: int
    side: str  # "buy" | "sell"
    good: str
    qty: int
    price: int  # limit (or worst acceptable price for a market order), in cents
    kind: str = "limit"  # "limit" | "market"
    expires_hours: float | None = None
    status: str = "queued"  # queued | resting | cancelling | filled | cancelled | expired | rejected
    queued_tick: int = 0
    placed_tick: int | None = None
    order_id: int | None = None  # id on the book once placed
    left: int = 0  # quantity not yet filled
    filled: int = 0
    value: int = 0  # gold traded so far (sum of qty * price)
    note: str | None = None


@dataclass
class Player:
    """The user's trading account: an owner of gold and goods in the ledger that is not an NPC."""
    opened_tick: int = 0
    orders: list = field(default_factory=list)
    next_id: int = 1
    injected_gold: int = 0
    injected_goods: dict = field(default_factory=dict)
    injected_value: int = 0  # gold value of everything injected, at the prices of the time
    fills: list = field(default_factory=list)
    actions: list = field(default_factory=list)  # the replay log
    networth: list = field(default_factory=list)  # one row per day
    basis: dict = field(default_factory=dict)  # good -> [qty, cost] for the average cost
    realized: dict = field(default_factory=dict)  # good -> cents
    taxes: int = 0
