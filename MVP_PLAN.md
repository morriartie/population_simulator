# MVP plan and status

Goal: show prices, inflation and trade emerging from agent behaviour while gold and goods are
strictly conserved (REQ-5.4, section 6).

## Acceptance checks

| check | where | status |
|---|---|---|
| Gold and goods conserved, apart from authority mints and regeneration/consumption | `test_simulation::test_conservation_and_escrow`, daily `conservation_daily.csv`, ledger replay | ✅ |
| A stimulus raises the price level | `test_acceptance::test_stimulus_raises_prices` (vs. same-seed control) | ✅ about 2× in A |
| Connecting cities narrows the price gap | `test_acceptance::test_road_narrows_wood_price_gap` | ✅ B wood about 20 → 8 gold |
| Sleep deprivation lowers `daily_max` and raises sickness probability | `test_biology` | ✅ |
| Higher skill lowers stamina per unit | `test_biology`, `summary.json` → `stamina_per_unit_by_skill` | ✅ |
| Same seed gives the same run, across processes and hash seeds | `test_simulation` | ✅ |

## Milestones

| | milestone | status |
|---|---|---|
| M0 | Skeleton: layout, ECS, YAML config, seeded RNG, pipeline | ✅ |
| M1 | Ledger: integer cents, authority/nature sources, escrow accounts, log replay | ✅ |
| M2 | Market: CDA order book, batch place/cancel with per-order results, escrow | ✅ |
| M3 | NPCs: stamina spring, metabolism, sickness, needs and urgency, decisions, pricing, starvation | ✅ |
| M4 | Work choice (income per stamina + preference + skill), skills, regenerating nodes | ✅ |
| M5 | World graph, travel, merchants with scouting, stimulus/road events | ✅ |
| M6 | CSV/JSON output, plots, pickle snapshots, acceptance tests | ✅ |
| M7 | REST service (stdlib), SQLite paired snapshots, restart loading, HTTP parity test | ✅ |
| M8 | uv project (`uv add`/`uv sync`, `uv.lock`), multi-stage Dockerfile (`api`/`sim`/`test`), docker-compose, env config (REQ-1B) | ✅ (verified with `uv sync` + `docker compose up --build`) |
| M9 | Dashboard: runs with map + scrubber, compare, scenario editor (form + YAML, validation), live play with controls and saving (REQ-1C) | ✅ |

## Deliberate departures from SPECIFICATIONS.md

- **Where the ledger lives (REQ-1A.5):** the ledger is in the simulation process, which is the
  source of truth for balances. The market API is the source of truth for orders and reports what
  is locked. Each city's escrow account is checked against the API's `locked` totals. Moving the
  ledger into the API would turn every meal into a network call.
- **Shelter:** shelter is met by using wood, as a stand-in until housing (REQ-3.7) exists.
- **Resource nodes:** nodes belong to a city's hinterland instead of being separate graph vertices.
  Roads connect cities.
- **Orders per tick:** two batch calls per city per tick (cancels, then placements) instead of one,
  so that escrow refunds settle before new orders are sized.
- **REST server:** built on the standard library rather than FastAPI (PyPI was unreachable while
  building, and it also keeps the Docker image free of dependencies).

## Next (post-MVP), roughly in order

1. Tune B's food glut. Options: storage spoilage, or merchants exporting more food.
2. Taxes and basic support (REQ-5.5): flat income/sales tax into city treasuries; support
   payments to NPCs below survival thresholds.
3. Contracts (REQ-1A.8): one `Contract` component and a `ContractSystem`; employer job board
   (REQ-3.2, REQ-4.3).
4. Housing with installments and debt (REQ-3.7), replacing the wood-shelter stand-in.
5. Medicine and luxury goods with one-step recipes (REQ-3.5); a "seek care" action.
6. Multi-day planning: forecast horizon of 3–7 days, stockpiling ahead of expected shortages
   (REQ-4.1.8).
7. Births (REQ-2.1.4).
8. Parallel systems with a fixed merge order (REQ-1A.3).
