# SPECIFICATIONS

Requirements derived from [INSTRUCTIONS.md](INSTRUCTIONS.md) for a society simulation with emergent macroeconomics.

## 1. General / Architecture

- **REQ-1.1** Use a bottom-up Agent-Based Modeling (ABM) approach.
- **REQ-1.2** Use an Entity-Component-System (ECS) design, mimicking Unity's architecture, in Python.
- **REQ-1.3** Entities (NPCs, cities, resource nodes) are plain IDs; components are pure data; systems hold all logic and iterate over entities with specific components.
- **REQ-1.4** The ECS is a fully custom, minimal implementation (dict of component stores) for full control and determinism. `esper` is not used.
- **REQ-1.5** The simulation advances in discrete ticks (hour or day granularity); every system processes relevant entities each tick.
- **REQ-1.6** Real-time execution is not required. The simulation may be precomputed, or stepped one tick at a time when ready. Tick length and population size are configurable.
- **REQ-1.7** Pure Python. Performance is not critical initially.
- **REQ-1.8** Runs are reproducible: seeded RNG and deterministic results.
- **REQ-1.9** Output is headless: CSV/JSON logs plus matplotlib plots.
- **REQ-1.10** Invariant tests with pytest (e.g. conservation of currency and resources).

## 1A. Software Architecture

- **REQ-1A.1 ECS core:** custom minimal ECS. Component stores are dicts keyed by entity ID.
- **REQ-1A.2 System pipeline:** a fixed, ordered pipeline per tick, configured in one place.
  - Example order: physiology, needs, decision, jobs, market clearing, tax, aging.
- **REQ-1A.3 Concurrency and determinism:** systems may run in parallel where it is safe to do so.
  - Results must stay deterministic and reproducible (REQ-1.8) regardless of parallelism.
  - Systems that touch shared state (market, ledger, contracts) run in a fixed order.
- **REQ-1A.4 Numeric types:**
  - Gold is stored as integer units (e.g. "cents") to avoid float drift.
  - Goods are integers or fixed-precision decimals.
- **REQ-1A.5 Ledger:** a central Ledger service that lives in the API (REQ-1A.6).
  - Every transfer of gold or goods goes through one API that validates and logs it.
  - Conservation checks (REQ-5.1) come from the ledger log.
- **REQ-1A.6 Market API:** a dedicated API runs as a separate process (network service) and can run in a Docker container. It controls boards and transactions.
  - The API owns the market boards, job boards and order books of all cities in all worlds.
  - Only one world runs at a time (no parallel worlds). The API serves one simulation client at a time.
  - API state persists across simulation sessions. Closing the simulation and loading another world does not reset the API.
  - Protocol: REST.
  - Isolation uses routes of the form `/{world}/{city}`.
  - An NPC can only see and interact with information from the city it is currently in.
  - Snapshots are paired: when the world saves a state, the API saves its state for that world too. Loading a world restores the matching API state for it.
  - Orders are accepted in batches: one call can carry one or multiple orders (placement and cancellation), to limit network calls per tick.
  - A batch can partially succeed: each order succeeds or fails independently, and the response reports the result per order.
  - Storage: the API keeps live state in memory. When a world saves a state, the API persists that world's state to a SQLite database. On startup, the API loads the latest snapshot of every world from SQLite into memory.
  - Restart behavior: in-memory changes made since the last save are lost if the API restarts. This is acceptable.
- **REQ-1A.7 Order funding (escrow):**
  - A bid locks gold and an ask locks goods.
  - The lock is released on match or cancel.
- **REQ-1A.8 Contracts:** loans, jobs and house installments are entities with a shared `Contract` component (parties, schedule, amount, status).
  - One `ContractSystem` processes them.
  - This supports the debt and installment needs in REQ-3.7.
- **REQ-1A.9 References:** components are pure-data dataclasses.
  - Entities reference each other by integer ID only (owner, city, parents, etc.), never by object pointers.
- **REQ-1A.10 Project layout:** `src/popsim/{components,systems,world,config,ledger,io}`.
  - Config is YAML.
  - Scenarios are config plus event scripts.
  - Snapshots use JSON/pickle for save/resume.

## 1B. Packaging, Environments and Containers

- **REQ-1B.1 uv project management:** dependencies and environments are managed with [uv](https://docs.astral.sh/uv/).
  - `pyproject.toml` is the single source of dependencies.
  - Runtime dependencies are added with `uv add <pkg>`; development-only tools with `uv add --dev <pkg>` (stored in `[dependency-groups]`).
  - `uv.lock` is committed. `uv sync` reproduces the exact environment; containers use `uv sync --locked`, which fails if the lock is out of date.
  - The Python version is pinned in `.python-version` and `requires-python`.
  - Commands run through `uv run` (e.g. `uv run popsim run ...`, `uv run pytest`).
- **REQ-1B.2 Containerized:** every component runs in a container; no host Python is required.
  - One multi-stage `Dockerfile`. A builder stage installs dependencies from `uv.lock` with uv; final images contain only Python, the virtual environment and the needed files (no uv, no build tools).
  - Build targets: `api` (market service, REQ-1A.6), `sim` (simulation runner with plots), `test` (sim plus the dev dependency group, runs pytest).
  - Images run as a non-root user.
  - The dependency layer is cached separately from the source, so code changes do not reinstall dependencies.
- **REQ-1B.3 docker-compose:** `docker-compose.yml` defines the services.
  - `api`: serves the REST API on port 8080, stores SQLite on a named volume, has a healthcheck on `/health`, restarts unless stopped.
  - `sim`: starts after `api` is healthy and talks to it through `POPSIM_API_URL`. Scenarios are mounted read-only; outputs are written to the host's `./runs`.
  - `test`: runs the test suite (compose profile `test`).
  - `docker compose up --build` starts the API and runs the default scenario. `docker compose run --rm sim run scenarios/<name>.yaml` runs any other scenario.
- **REQ-1B.4 Environment configuration:**
  - `POPSIM_API_URL`: market API base URL for the simulation; unset means the in-process market.
  - `POPSIM_DB`: SQLite path for the API.
  - `POPSIM_PORT`: API port.
- **REQ-1B.5 Build context:** a `.dockerignore` excludes virtual environments, caches, run outputs, snapshots and SQLite files.

## 1C. Dashboard

A local web dashboard to inspect results, configure scenarios and play runs live.

- **REQ-1C.1 Delivery:**
  - Served by `popsim dashboard` (`uv run popsim dashboard`) and by a `dashboard` docker-compose service on port 8000 (REQ-1B.3), which starts after `api` is healthy.
  - The backend uses the Python standard library only (like the market API). The frontend is plain HTML/CSS/JavaScript with no build step and no external CDN, so it works offline.
  - Configuration: `POPSIM_DASHBOARD_PORT`, `POPSIM_RUNS_DIR`, `POPSIM_SCENARIOS_DIR`, and `POPSIM_API_URL` for live runs (REQ-1B.4).
- **REQ-1C.2 Runs (results):**
  - Lists every run folder under the runs directory with scenario, days, population, deaths, conservation status and date.
  - The run view replaces the PNG plots with interactive charts: prices per city and good (log/linear scale, smoothing, event markers, hover values), society panels (population, mean gold, Gini, satiety, daily_max, sick), and cumulative trade flows by route.
  - A world map shows cities, roads and per-city prices. When cities have coordinates (REQ-3A.1) it draws them at their positions, to scale, with each road's length, hours and stamina cost; otherwise it keeps the current even layout.
  - **City view:** clicking a city on the map opens a local map of that city: its radius, workplaces, houses and citizens as dots (resource nodes lie outside cities and are drawn on the world map, REQ-3A.11), with walkers moving toward their destination. Clicking a dot opens it in the inspector (REQ-1C.5). In a live run it updates with each frame. A timeline scrubber moves the map and headline figures through the days of a finished run.
  - Tables for deaths, events and the daily conservation checks.
  - Compare: overlay the same city/good series from two or more runs.
- **REQ-1C.3 Scenarios (configuration):**
  - Form editor for the scenario format: name, days, seed, treasury share, merchant share; cities with population, optional `x`, `y` and radius, `housing_pct` (REQ-3.7) and workplaces with optional positions (REQ-3A.1); resource nodes with their branch road and junction (REQ-3A.11); roads (length, with hours and stamina cost shown as derived values until overridden); events (stimulus, mint, add/set/remove road) at a day and hour.
  - A raw YAML tab for every other setting, kept in sync with the form.
  - A **Map** tab to place and edit cities, roads, resources, workplaces and houses visually (REQ-1C.8), kept in sync with the form and the YAML.
  - A `player:` section (`gold_share`, `gold`, `goods`) sets the opening balance of the player account used by the Market tab (REQ-1C.9).
  - Validation before saving: road and event cities must exist, numbers must be in range, and the file must load with `extends`.
  - Scenarios are saved to the scenarios directory as `<name>.yaml`; "new from existing" copies one.
- **REQ-1C.4 Play (live runs):**
  - Start a run from a scenario with optional seed and days overrides, or continue a saved run from its snapshot (REQ-1A.10); a continued run that had reached its last day starts finished and can be extended.
  - Controls: play, pause, step one hour, step one day, speed (days per second).
  - Charts and the world map update live through a server-sent event stream.
  - Stop & save writes the standard run output (REQ-1.9) plus a paired snapshot to `runs/<name>/`, so the run appears in the run list.
  - One live run at a time (REQ-1A.6). Markets use the API at `POPSIM_API_URL` when set, otherwise the in-process market.
  - A live run's results are identical to `popsim run` with the same scenario and seed (REQ-1.8).
  - A **Market** tab trades by hand on the live world (REQ-1C.9). A live run in which the user has traded is no longer expected to equal `popsim run`; it equals a replay of its recorded player actions.
- **REQ-1C.5 Inspector:** list every citizen of a city and open one to see everything about them. Available in the live view (REQ-1C.4) and, for runs saved with a snapshot, in the run view (REQ-1C.2).
  - **Source:** a live run reads the current state; a finished run reads its paired snapshot (REQ-1A.10). The timeline scrubber is not supported for citizens, because only the final state is stored. The view is read-only and never changes the simulation or its determinism (REQ-1.8).
  - **Citizen list:** pick a city (a "travelling" entry lists NPCs on a road) and see all its citizens in a table with: id, name, role (worker or merchant), age, gender, gold, satiety, stamina, sick, and current action. Sort by any column; filter by role, sick, and a gold or satiety range; search by name or id. Sorting and filtering happen on the server, with paging for large cities. The list refreshes with each live frame.
  - **Citizen detail:** opens on click, as a side panel with the sections below. It follows the NPC when it moves to another city, and also shows an NPC that has died (final state and cause).
    - **Status:** age, life expectancy, gender, home (house and city) and current location with its x, y position (or the road being travelled, with ticks left); when walking, the destination and arrival time; stamina (current, daily max, absolute max, sleep hours today, good rest streak), satiety and burn rate, sickness (sick flag, probability, immune strength).
    - **Needs:** level, comfort, urgent and critical thresholds, urgency, and hours at zero for each need (REQ-4.2.2, REQ-4.2.3), with a bar that marks the thresholds.
    - **Wallet and inventory:** gold, goods by quantity, and gold or goods locked in open orders (REQ-1A.7).
    - **Traits and skills:** risk, urgency tolerance, thrift, luxury, forecast noise, reactivity, need weights, job preference, stock days, and skill levels.
    - **Current action:** kind, target, ticks left, and for merchants the mode, good, destination, quantity and maximum buy price. The reason for the choice is shown when recorded (REQ-1C.6).
    - **Open orders:** side, good, remaining quantity, limit price, city, and age in ticks, plus the bid premium and ask discount per good.
    - **Spending plan:** what the NPC intends to buy and keep, with each item's source in the model. It shows the savings target (REQ-4.2.1), the stock target per good (days of stock), the current reservation buy price and reservation wage per good, and the price forecast (EMA) per good and city that these use (REQ-4.1.5, REQ-4.1.8). Pending payments (loans, installments) appear here once contracts exist (REQ-1A.8, REQ-3.7). Values are those the decision system computes now, not a separate copy.
    - **Purchase and sales history:** every fill the NPC took part in: tick and day, city, good, side, quantity, price, total, and counterparty id. It has totals per good, average price paid against the city average, and gold spent against earned per day, with a chart (REQ-1C.6).
    - **Actions history:** a timeline of the actions the NPC committed to (kind, target, start and end tick), including travel, sleep and interruptions by urgent needs (REQ-4.1.2).
    - **Jobs and income:** the current job or gathering task (good, node, city, position, distance from the NPC) and its expected income per stamina, with the commute counted (REQ-3A.7); the options list shows each job's distance and walk stamina. History of tasks worked, with hours, units gathered and wages or sales income. Public jobs and wages (REQ-5.5) are included once they exist; contracts of REQ-1A.8 are listed with parties, schedule, amount and status.
    - **Memory:** the remembered prices, per city and good, with the day each was last seen (REQ-4.1.6).
  - **Links:** a counterparty id in a history opens that citizen; a city name opens its citizen list.
  - **Endpoints:** `GET /api/session/cities/{city}/citizens` (query: sort, order, filter, search, page) and `GET /api/session/citizens/{eid}`; the same two for a saved run under `/api/runs/{name}/`. A missing citizen or run returns 404, and a saved run without a snapshot returns a clear error.
- **REQ-1C.6 Citizen records:** the inspector's histories need data the simulation does not keep today, so the simulation records it.
  - Each NPC has a bounded record (ring buffers with a configurable size, default about 500 entries per kind), as a pure-data component (REQ-1A.9) holding fills, committed actions, job and task history, daily gold and spending totals, and a short reason for each decision (the scores of the options at the time of choice).
  - Fills are taken from the ledger and market transactions (REQ-1A.5), and actions from the decision system (REQ-4.1.2). Recording only reads simulation state: it must not use the RNG or change any decision, so a run with recording on or off gives identical results.
  - Recording is on by default and can be turned off in the scenario config (`inspector.record`) to save memory. When off, the inspector shows current state only and marks the histories as "not recorded".
  - The records are stored in snapshots (REQ-1A.10), so a resumed run keeps the history. Records of dead NPCs are kept for a configurable number of days (default 30), then removed.
  - The extra memory is bounded by the population times the buffer sizes; it must stay small enough for the default scenarios on the dashboard container.
- **REQ-1C.7 Tests:** endpoint tests for runs, scenarios and validation; a live-session test (start, step, pause, save, output listed); and the live-vs-CLI equivalence check. Also:
  - the citizen list for a city returns exactly the NPCs located there, and sorting, filtering, search and paging work;
  - the citizen detail matches the simulation state: wallet, inventory, needs and open orders agree with the ledger and market;
  - purchase history totals match the ledger's transfers for that NPC;
  - a run with recording on and off gives identical results (REQ-1.8), and the same for a run with the inspector queried every tick;
  - a saved run without a snapshot, an unknown city and an unknown citizen return clear errors;
  - history survives a snapshot and resume, and the buffers stay within their limits.
  - positions and movement (REQ-3A): a scenario with no coordinates gives the same CSV results as before; road `hours` and `stamina_cost` follow the straight-line distance and the movement rates, and an explicit value overrides them; a road `length` below the straight-line distance is rejected; a mixed scenario (some cities with coordinates, some without) is rejected; walking time and stamina equal distance over speed and distance times the rate; a job at another position starts only after arrival; a farther node of equal pay is chosen less often; an NPC cannot take a job it cannot reach with its stamina; positions survive a snapshot and resume; generated positions do not change the main RNG state; an NPC can place orders while walking and from any position in its city; a job's net income per stamina counts the walk both ways, while its income per hour counts only the hours at the job's position and is the same at any distance; sleeping at a house restores stamina at the home multiplier and anywhere else at the base rate, and an NPC does not walk to a house that costs more stamina than the bonus gives; walking speed is identical for sick, old and healthy NPCs.
  - housing and overworld nodes (REQ-3.7, REQ-3A.11): the generated house count of a city equals `round(population * housing_pct / 100)` for 80, 100 and 120, and manual houses are added on top of it, with the global default and with a city override; houses are created at the same positions for the same seed; a moved house keeps its override and a lowered count drops the overrides above it; a node inside a city, an unknown road and `t` outside 0-1 are rejected; a branch is no shorter than the straight line to the node; the gathering trip's stamina and hours equal the main-road part plus the branch, both ways; an NPC away from a city cannot trade and sells at the city where it arrives; removing the main road makes the node unreachable without deleting it; conservation holds across all of it.
  - eraser (REQ-1C.8): it deletes only the checked kinds, and only items inside its radius; an item just outside the circle stays; generated houses are never deleted; erasing a city asks for confirmation and removes its roads and events; a cancelled stroke changes nothing; one stroke is one undo step; the result is a scenario that still validates, or reports its errors on the items concerned.
  - map editor (REQ-1C.8): the preview of automatic positions equals the positions the simulation builds for the same scenario and seed; the auto-layout, connect and scatter helpers are deterministic and always produce a scenario that validates; validation errors carry a path that names the item they belong to; a scenario edited through the map keeps every key the editor does not know; deleting a city removes its roads and events and nothing else.
  - market tab (REQ-1C.9): the player has its own wallet and is not in the citizen list, the population or the Gini; the opening grant is the only creation of gold or goods and appears in the ledger; conservation holds after many player trades; a buy and a sell between the player and an NPC settle both sides, taxes included, and update the NPC's open order and record; a market order never leaves a rest on the book; an order queued while paused is not matched until time moves, then runs in the next tick before the NPCs' orders of its city, in queue order; a queued order locks its funds at once, and editing or cancelling it releases or changes them; a limit order that crosses the book fills at the book's prices, and one that does not rests; cancelling a resting order runs at the next tick and reports a fill that came first; an order the player cannot fund is refused and locks nothing; player orders do not expire by the NPC time to live; a world with a player that never trades runs identically to one without, apart from the grant (and the shuffle sees the player's orders only when there are some); top-ups are logged, change the wallet at once, are counted as injections in the conservation checks and are excluded from the shown profit; the player's orders are shuffled with the NPCs' orders by the seeded RNG, and the player's own orders keep their queue order; replaying the saved action log gives the same results; the account, orders and history survive a snapshot and resume; the endpoints return clear errors with no live run, an unknown city or good, and a bad quantity or price; two orders sent at once are queued one after the other.
- **REQ-1C.8 Map editor:** a visual way to build a scenario's world in the Scenarios view. The same scenario can be edited in the form, the YAML or the map, and the three always agree.
  - **Canvas:** the plane of REQ-3A.1 in km, with pan and zoom, a grid with a scale bar, the coordinates under the cursor, optional snapping to the grid, undo and redo (at least 50 steps), Delete to remove the selection and Esc to cancel a tool. Cities are drawn as circles of their radius, roads as lines, and the other items as markers.
  - **Tools:** Select/move, City, Road, Eraser, and three add buttons in the toolbar: **Add house**, **Add resource point** and **Add job**. A side panel edits the selected item with the same fields as the form (REQ-1C.3), including each city's own tax policy (REQ-5.5).
  - **Cities:** click to place; a new city gets the next free name, a default population and the default radius. Drag to move (its houses and workplaces move with it, since they are offsets from its center; resource nodes do not, since they belong to roads, REQ-3A.11) and drag the edge to change the radius. Items that would fall outside a shrunk radius are shown in red and listed, not silently dropped. Deleting a city asks for confirmation and lists what goes with it (its roads and the events that mention it).
  - **Roads:** drag from one city to another. The map shows the straight-line length and the derived hours and stamina cost (REQ-3A.2); the panel can override any of them, including a longer `length` for a winding road. A road to itself or a second road between the same two cities is refused. "Opens on day N" turns the road into an `add_road` event instead of a road open from day 0.
  - **Placing things, by hand or automatically:** each of the following can be placed by hand or left to the simulation.
    - **Add buttons (click to place):** clicking a button arms that tool, and the next click on the map places one item of that kind; the tool stays armed for more clicks until Esc, a right click or the button is pressed again. The cursor shows a ghost of the item, in red where placing is not allowed. A click that places an item also selects it and opens its panel (good for a node, task and wage for a job).
      - **Add job:** the click must be inside a city; the workplace is stored as an offset from that city's center.
      - **Add house:** the click must be inside a city; the house is stored as a manual house of that city (REQ-3.7).
      - **Add resource point:** the click must be outside every city. The branch road is drawn at once to the nearest road, using the rule of REQ-3A.11, with the junction shown as a handle that can be dragged along its road or onto another road.
      - Placed items can be dragged (workplaces and houses stay inside their city, nodes stay outside), and deleted with Delete. Undo and redo cover every placement.
  - **Eraser:** a tool that deletes the items under a circle, many at a time.
    - **Size:** a radius in km, set with a slider and a number field (default 1, from 0.1 to 50). The cursor shows the circle at that size, and it grows and shrinks with the zoom. `[` and `]` change the size from the keyboard.
    - **What it deletes:** a checkbox for each kind: Houses, Jobs, Resource nodes, Roads and Cities. Only checked kinds are touched. Houses, Jobs and Resource nodes are checked by default; Roads and Cities are not, since they take other things with them. The choice is kept for the session.
    - **Which items count:** an item is hit when its position is inside the circle. A road is hit when its straight line crosses the circle, and a city when its center is inside it. A node's branch road is not a road of its own: it goes with its node, and is never hit alone.
    - **Using it:** click, or hold and drag, to erase along a path. Items that would be erased are drawn in red while the circle is over them, and nothing is deleted until the button is released. Esc during a stroke cancels it. One stroke is one step of undo and redo (REQ-1C.8 canvas).
    - **Things that go with others:** erasing a city removes what is listed for deleting a city above (its roads and the events that mention it). Erasing a road leaves the nodes on it in place but unreachable (REQ-3A.11). When a stroke would remove a city, or a road that has nodes or events, a confirmation lists what goes with it before anything is deleted.
    - **Generated houses:** their count follows `housing_pct` (REQ-3.7), so the eraser skips them. Only manual houses are deleted, and the panel says how many generated houses were skipped.
    - **Inherited items:** items from the `extends` parent are handled as in the Inheritance rule below: the parent's list is copied into this scenario first, and the panel says so.
    - This is a tool, not a selection, so it does not change the out-of-scope note on selecting several items.
    - **Houses:** the city's `housing_pct` field shows the generated house count live, and the manual houses are listed beside it. Generated and manual houses are both drawn and both can be dragged; generated ones are told apart by a lighter marker. Only manual houses can be deleted by hand. Generated houses can be moved but not removed, since their count follows the percentage.
    - **Automatic:** an item with no position is placed by the simulation, reproducibly (REQ-3A.9). The map shows where, as a faded marker marked "auto". "Place by hand" turns it into an explicit position, and "Place automatically" clears the position again.
    - **Areas:** a Scatter gesture draws a circle inside a city, with a good (or task), a count and a richness. It creates that many ordinary workplaces or manual houses inside a city, or resource nodes outside cities (each branching from the road nearest to its position, REQ-3A.11), at reproducible positions inside the circle. Scatter of houses creates manual houses (REQ-3.7). The area is a way of authoring: the scenario stores the individual items, which can then be edited or moved one by one, and the simulation model stays points (REQ-3A.1).
  - **Preview of automatic positions:** the server builds the places of the scenario (REQ-3.8) and returns the layout (`POST /api/scenarios/preview`), so the map shows exactly the positions the simulation would use for this scenario and seed. It refreshes a moment after each edit.
  - **Helpers:** all computed by the server, so they are deterministic and testable.
    - *Arrange cities*: gives cities without coordinates positions that do not overlap, spaced by their radii.
    - *Connect cities*: adds roads so that no city is cut off from the rest (a minimum spanning tree of the straight-line distances), plus optionally each city's nearest neighbors.
    - *Fill in resources*: gives each city access to a node of every good that it lacks, placed outside cities on a branch road from one of its roads, with default stats. A city with no road cannot be filled in and is reported.
    - *Fill in workplaces*: adds a chosen number.
    - Every helper shows what it will add before applying it and is one undo step.
  - **Scenarios without coordinates:** the Map tab offers "Give cities positions" (Arrange cities). It warns that this makes the scenario spatial, so people walk and results differ from the version without coordinates (REQ-3A.9); "Remove positions" goes back.
  - **Problems on the map:** validation runs after each edit and its errors are attached to the item they concern (the response gives each error a `path` such as `cities[1].nodes[0].at`). Items with problems are outlined; clicking a problem selects the item. Warnings include a city with no road, two cities overlapping, and a resource that no one can reach.
  - **Inheritance:** items that come from the `extends` parent are drawn dimmed. Editing one copies the parent's list into this scenario first, as the form does, and says so.
  - **Robustness:**
    - The editor never drops keys it does not know: everything in the scenario that the map does not edit is preserved as it was.
    - Names stay unique, numbers are kept in range (population 1-5000), and the number of cities (50), nodes per world (500) and an area's count are capped so a scenario can always be built.
    - A draft is kept in the browser after every edit and offered again after a reload or crash; leaving with unsaved changes asks first.
    - Every action on the map is also possible in the form, so the map is never the only way.
    - The map works with the keyboard: Tab moves between items, the arrow keys move the selected item by one snap step, Enter opens its panel.
  - **Out of scope here:** polygon regions, terrain, roads drawn as bent lines (use `length`), selecting several items at once, and touch gestures.
- **REQ-1C.9 Market tab:** a **Market** tab where the user trades by hand, as a player, on one world's markets (REQ-1A.6), at the same time as the NPCs.
  - **World:** the tab trades on the live run's world (REQ-1C.4) and shows its name, day and hour. With no live run it offers to start one from a scenario, or to continue a saved run that has a snapshot (REQ-1A.10). Continuing is also available to Play: a live run can start from a saved run's snapshot and resumes at its saved tick, with its market, citizens and player account. It has the play controls (play, pause, step, speed) so time can be moved without leaving the tab. Only one world is live at a time (REQ-1A.6), so there is no switching between worlds while a run is open; to trade on another, stop or save this one first.
  - **Player account:** the first time the user opens the tab on a world, the simulation creates one **player** entity. It is not an NPC: it has no needs, never dies, is not counted in population, Gini or mean gold, and is not shown in the citizen list (REQ-1C.5). It is a pure-data component (REQ-1A.9) with a wallet and an inventory in the ledger (REQ-1A.5), like any other owner of gold and goods.
    - **Opening balance:** gold and goods come from a central-authority grant, logged in the ledger as such (REQ-5.1). The player exists to move the market, so the default is large: gold equal to `player.gold_share` (default 0.5) of the total gold of the NPCs at the start, and no goods, since it can buy them. The scenario's `player:` section can set `gold_share`, or an exact `gold` amount, and `goods` per good; setting them to zero starts the player with nothing. The conservation checks (REQ-5.1, REQ-1.10) count every grant as an explicit injection.
    - **Top-up:** the user can add gold and goods to the account at any time during the run, in any amount, with the Top up control of the tab. A top-up is a central-authority injection like the opening grant: it is logged in the ledger with its tick and amount, applied at once (it matches nothing, so it needs no scheduling), and kept in the player's action log (see Time and determinism). The tab shows the total injected so far, per gold and per good, next to the net worth, and net worth and profit are always shown **net of** injections, so top-ups are never mistaken for trading gains. Injections are the only way the player gets money or goods other than trading; there is no way to take them out except by selling, and no way to touch NPC wallets or treasuries (see Safety).
    - Selling and buying are the player's only other ways to change its holdings. Taxes and fees (REQ-5.5) apply as for an NPC.
    - The account is saved in snapshots and survives a resume with its holdings, open orders and history (REQ-1A.10).
  - **God mode:** the player stands outside the world. It trades in any city of the world, one at a time, chosen in the tab, and never travels, rests or eats. The player sees every city's board; NPCs still see only their own city (REQ-4.1.6) and learn nothing from the player except through the prices and the book.
  - **Market view** for the chosen city:
    - A **goods table:** each good with last price, best bid, best ask, spread, volume of the last day, change against the previous day, and the player's holdings of it. Click a good to select it.
    - The **order book** of the selected good: bids and asks by price level with quantity (depth as in REQ-1A.6, up to 10 levels per side), with the player's own orders marked.
    - A **price chart** of the selected good in that city (last price and daily volume), with the same options as the run view (REQ-1C.2); it updates as the simulation advances.
    - **Cross-city view:** the same good's price in every city side by side, so a route worth trading can be seen at a glance. This is information only: an order is always placed in a single city.
  - **Order ticket:** side (buy or sell), good, quantity, order type and price.
    - **Limit order:** quantity and price. When it is executed (see Scheduled orders), it is matched against the book (REQ-4.4); what is not filled stays on the book.
    - **Market order:** quantity and a worst acceptable price (required, to limit slippage). At execution it is a limit order at that price whose unfilled rest is cancelled right away and never stays on the book.
    - **Time in force:** an order stays until it is filled or the player cancels it. It does not expire by the NPC time to live (`pricing.order_ttl_hours`). The player may set an optional expiry in hours.
    - **Funding (REQ-1A.7):** when the order is queued, a buy locks `qty × price` gold and a sell locks the goods, from the player's wallet and inventory, in the city's escrow account. The ticket shows the cost or proceeds, the sales and income tax of the city (REQ-5.5), and what is left in the wallet, **before** the order is queued. An order the player cannot fund is refused with the reason (not enough gold, not enough goods), and nothing is locked. The ticket also shows an estimate from the book as it is now ("at today's asks this would fill at about X"), marked as an estimate, because the book can change before the order runs.
    - Quantities are whole units and prices whole cents (REQ-1A.4); the form refuses anything else.
  - **Scheduled orders:** an order is not matched when the user sends it. It is queued, like a scheduled call, and is considered when the simulation runs again.
    - **Queue:** a queued order has the status `queued`. It sits in the player's account (pure data, REQ-1A.9) with its funds locked, and it is part of the saved state (REQ-1A.10). While queued it can be edited or cancelled, and the funds are released at once on cancel.
    - **Execution:** at the next tick that runs, in the market system (REQ-4.4), the queued orders of that city are sent to the market API as one batch (REQ-1A.6) together with the NPCs' orders of that city. The player's orders are mixed into the same seeded shuffle as the NPCs' orders (REQ-1.8), so they have no priority over them in arrival order; orders of the player among themselves keep the order they were queued in. Each then has a result: `filled`, `partly filled` (the rest rests on the book), `resting`, or `rejected` with the cause. A market order's rest is cancelled and its funds released.
    - **Settlement:** every fill is settled from escrow through the same path as an NPC's, including the taxes (REQ-5.5) and the NPC counterparty's updates (its open order, price adaptation and record, REQ-1C.6). Both sides of every fill are in the ledger, so conservation holds with the player in the world (REQ-5.1).
    - **Several ticks:** if the user steps one hour, only the orders queued before that tick run in it. Orders queued while the simulation is running are picked up by the next tick, so the delay is at most one tick.
    - **After execution:** a resting order stays on the book until filled or cancelled. Cancelling a resting order is also scheduled: it is a queued cancel, executed at the next tick before new orders, and shows `cancelling` until then. An order may be filled by an NPC before the cancel runs, and the result then says so.
  - **Wallet and positions:** the player's gold, goods with quantity and value at the mean of the cities' last prices (the same prices for net worth, injected value and profit, so they always agree; goods without a price yet use the scenario's starting price), gold and goods locked in open orders, and net worth (gold plus goods at last prices), the total injected (opening grant and top-ups) and the profit = net worth minus injected, as numbers and a chart over time. The **Top up** control sits here.
  - **Open orders:** a table of the player's orders in the world with a status (`queued`, `resting`, `cancelling`), city, good, side, quantity left, price and age, and with cancel for each one and Cancel all. Cancelling a queued order is immediate; cancelling a resting one is scheduled (see Scheduled orders). A partial fill shows the part filled.
  - **History:** every fill the player took part in (tick and day, city, good, side, quantity, price, total, tax paid, counterparty id, and whether the player was the maker or the taker), with totals per good, and the player's realized profit per good by average cost. The history is also kept in the run's output when the run is saved (REQ-1.9) as `player_trades.csv`, with the action log as `player_actions.json`.
  - **Time and determinism:**
    - Time moves only by the play controls (REQ-1C.4). When paused, the player can queue, edit and cancel orders and read the books, but nothing is matched until time moves.
    - The queue is part of the simulation state, and orders run inside the market system at a fixed point of the pipeline (REQ-1A.2), so a tick is never half done. A player action is logged with the tick it was queued at; the saved run keeps this log, and replaying the same scenario, seed and action log gives the same results, tick for tick (REQ-1.8). A run with a player is not claimed to equal the same scenario run without one.
    - Player actions use no RNG. A world in which the player never queues an order gives the same results as one without a player account, except for the account's own grant (REQ-5.1).
  - **Safety:** the tab only acts on the live world. It has no way to edit a finished run's files, an NPC's wallet, a treasury or the ledger. A second browser tab or window on the same live run sees the same account, and orders are queued one at a time in the order they arrive. Orders are refused while the run is finished, discarded or failed.
  - **Updates:** the tab uses the live stream (REQ-1C.4): books, prices, wallet and open orders refresh with each frame, and immediately after the user's own action (a queued order appears at once, with its result after the next tick). If the stream drops the tab shows it as disconnected and disables the order ticket.
  - **Endpoints (dashboard server):** the dashboard server offers a player API for the live run, for example `GET /api/session/market/{city}` (goods table, book of a good, own orders), `GET /api/session/player` (wallet, holdings, history), `POST /api/session/player/orders` (queue an order), `PUT /api/session/player/orders/{id}` (edit a queued one) and `DELETE /api/session/player/orders/{id}` (cancel). Errors name the cause (no live run, unknown city or good, bad quantity or price, cannot fund, order gone). The dashboard server only queues orders in the simulation; it never talks to the market API behind the simulation's back, so the ledger and the book cannot disagree.
  - **Out of scope here:** a player who has to travel, rest or eat; several players; trading in a finished run; loans, contracts or short selling (REQ-1A.8); automated strategies, stop orders and bots; real-money or leaderboards.

## 2. Components (Data)

### 2.1 Biological / Physiological
- **REQ-2.1.1 Stamina:** `current_stamina`, `daily_max_stamina`, `absolute_max_stamina`.
  - Skipping sleep lowers `daily_max_stamina` ("spring elasticity").
  - It recovers slowly over consecutive days of good rest.
- **REQ-2.1.2 Metabolism:** `satiety_level`, `caloric_burn_rate`. Moving or working raises the burn rate.
- **REQ-2.1.3 Health:** `sickness_probability`, `immune_strength`.
  - Immunity drops when `daily_max_stamina` is low.
  - Sickness reduces work efficiency and increases rest needs.
- **REQ-2.1.4 Demographics:** age, gender, life expectancy.
  - Life cycle is simulated: aging, death, and births change the population.

### 2.2 Economic
- **REQ-2.2.1 Wallet:** a commodity money (e.g. gold) with a **fixed total supply** as the medium of exchange.
- **REQ-2.2.2 Inventory:** map of resource ID → quantity (e.g. `{wood: 10, food: 2}`).
- **REQ-2.2.3 Skills/Proficiencies:** float multipliers per task (e.g. Mining 1.2, Farming 0.8). Higher skill means less stamina spent per unit gathered.
- **REQ-2.2.4 Risk/Urgency Tolerance:** float controlling willingness to overpay for food when starving, or to undercut competitors when desperate for work.

## 3. World

- **REQ-3.1 Resource Nodes:** `YieldComponent` (amount of wood/stone, etc.), `RegenerationRate`, `Location`. Each node has a position on the overworld, outside every city, and is joined to a road by a branch road (REQ-3A.11). It belongs to no city. It is a workplace (REQ-3A.4).
- **REQ-3.2 Cities:** economic hubs with a `MarketBoard` (goods) and a `JobBoard` (labor contracts). A city has a position and an extent (REQ-3A.1).
- **REQ-3.3** Cities and resource nodes are entities with components, like NPCs.
- **REQ-3.7 Housing:** each city has a limited number of houses, generated from its population.
  - **Count:** `houses = round(population * housing_pct / 100)`, never below 0, using the city's initial population. This is the number of generated houses. 120 gives more houses than citizens (some stand empty), 100 one each, 80 gives `0.8 * population` houses (the rest of the citizens have no house, REQ-3A.8). It is computed once when the world is created and does not change during a run.
  - **`housing_pct`:** a global default in the scenario, which each city can override. It must be 0 or more. When it is set nowhere, no houses are generated, so scenarios written before this requirement keep their results; the Map tab writes `housing_pct: 100` into every city it creates.
  - **Generated and manual:** the builder creates the generated houses inside the city at deterministic positions (REQ-3A.9). The scenario can also list manual houses, `houses: [{at: [dx, dy]}]`, placed by hand in the Map tab (REQ-1C.8). The city has `generated + manual` houses, so manual houses are on top of the percentage and never replace generated ones. Who gets which house is decided as in REQ-3.8.
  - **Moving:** any house can be moved in the Map tab (REQ-1C.8). A moved manual house just changes its `at`. A moved generated house is stored as `house_positions: [{index, at}]` for the moved ones only, where `at` is an offset from the city center (REQ-3A.1) and `index` is the house's order in the city. Overrides with an `index` at or above the new count are dropped when `housing_pct` or the population is lowered, and the editor lists them first.
  - Houses have owners, who can sell them or pass them down.
  - Purchases can be paid in installments (days, months, years), so debt must be supported.
  - NPCs with pending payments plan what to pay and when, as part of REQ-4.1.8.
- **REQ-3.5 Goods:** one abstract good per basic need, plus luxury.
  - Food (including water).
  - Health (medicine/care).
  - Other basic needs, one good each (to be defined).
  - Luxury.
  - First-version goods: food+water, medicine, luxury, shelter (housing), a raw material (wood), and a tool/stone good for jobs.
  - Raw goods come from resource nodes.
  - Processed goods (medicine, luxury, tools, etc.) need work to be produced, using simple one-step recipes from raw goods.
  - Multi-step production chains are out of scope initially.
- **REQ-3.6 Topology:** the world is a graph of cities and resource nodes. Travel along an edge costs stamina and time. Edges have a length (REQ-3A.2); inside a city, movement is by walking (REQ-3A.5).
- **REQ-3.4** Cities start mathematically isolated. Roads or connections can be added later and lower travel stamina cost. Travel cost and time come from the road's length unless set explicitly (REQ-3A.2).
- **REQ-3.8 World creation:** the world is created in two steps.
  - **Places first:** cities, roads, resource nodes and their branch roads, workplaces, and the houses generated from `housing_pct` (REQ-3.7), with their positions (REQ-3A.1).
  - **Citizens after:** they are put into the finished world. Each gets its home city, a house if one is free and a starting position, all chosen from what the world already holds. The gold for them, and for the treasuries, is minted at that point (REQ-5.1).
  - The steps can run separately. Tools that only need the places, like the scenario editor's preview, stop after the first; its quick checks add a small sample of citizens to exercise the second.
  - Creating the world in two steps gives the same world, and the same run, as creating it at once (REQ-1.8).
  - Adding citizens to a world that already has some is not part of this version: it would create money in the middle of a run, which REQ-5.1 allows only through an explicit central-authority event.

## 3A. Space and Movement

Cities, resources, jobs and houses have positions. Moving between positions takes time and stamina, so distance shapes where people work, where they live and where merchants trade.

- **REQ-3A.1 Positions:** the world is a flat 2D plane with `x`, `y` coordinates in one abstract distance unit (default 1 unit = 1 km).
  - **Cities** have a center `x`, `y` and a `radius`. Everything inside a city (workplaces, houses) lies within that radius of the center. Resource nodes lie outside every city (REQ-3A.11).
  - **Resource nodes, workplaces and houses** have a `Position` component (pure data, REQ-1A.9) with absolute `x`, `y`.
  - In a scenario, a workplace or house inside a city gives its position as an offset from the city center (`at: [dx, dy]`). A resource node gives absolute coordinates (`at: [x, y]`). The builder stores absolute coordinates.
  - **NPCs** have a current `Position`. It changes while they walk, and while they travel between cities it moves along the road. It is the source of truth for where an NPC is. `Location.city` stays and is derived from it: the city whose area contains the NPC, or none on a road.
  - Positions are optional. Where one is not given, the builder generates it deterministically (REQ-3A.9). A city without coordinates is placed by the builder, so legacy scenarios keep working.
- **REQ-3A.2 Roads and distance:** a road joins two city centers and has a `length`.
  - `length` defaults to the straight-line distance between the two cities. It can be set higher for a winding road, never lower than the straight-line distance.
  - `hours` defaults to `length / movement.road.speed`, and `stamina_cost` to `length * movement.road.stamina_per_unit`.
  - An explicit `hours` or `stamina_cost` on the road or on an `add_road` / `set_road` event overrides the derived value. Scenarios that give both explicitly and no coordinates behave as before.
  - Roads between cities are the only way to leave a city (REQ-3.6); branch roads (REQ-3A.11) lead only to resource nodes. The graph, the travel stamina and time (REQ-3.4) and the merchant trip cost (REQ-4.5) all use the final `hours` and `stamina_cost`.
- **REQ-3A.3 Movement parameters (config):** `movement.walk` (inside a city) and `movement.road` (between cities) each have `speed` (units per hour) and `stamina_per_unit`.
  - Walking raises the metabolism burn rate (REQ-2.1.2) with the existing travel multiplier.
  - Defaults are chosen so a walk across a typical city takes under an hour and a road between cities keeps today's scenario values (about 8 hours and 20 stamina for the arbitrage road).
- **REQ-3A.4 Workplaces and jobs:** a job is performed at a position.
  - A gathering job is performed at its resource node's position (REQ-3.1), out on the overworld, reached by the trip of REQ-3A.11.
  - Other jobs (public jobs such as road building and guard duty, REQ-5.5, and job contracts, REQ-1A.8) are workplace entities with a `Position`, a city, a task type and a wage. Workplaces are created by the scenario (`workplaces:` per city) or by events.
  - The job board of a city (REQ-3.2) lists the workplaces of that city with their positions.
- **REQ-3A.5 Walking:** `walk` is a committed action (REQ-4.1.2) with a destination position.
  - It takes `ceil(distance / walk.speed / hours_per_tick)` ticks (at least one) and costs `distance * walk.stamina_per_unit` stamina, spread over the ticks. The NPC's position moves in a straight line to the destination.
  - Walking is interrupted like other commitments, by an urgent need (REQ-4.1.2). The NPC then stays where it is, at its current position.
  - Walking happens inside a city. Between cities an NPC uses the existing `travel` action (REQ-3A.2).
  - **Market access:** an NPC can buy and sell from anywhere in its city, including while walking. The market has no position and its prices do not depend on where the NPC stands.
  - Walking moves no gold or goods, so conservation (REQ-5.1) is unaffected.
- **REQ-3A.6 Performing a job:** an NPC performs a job only while within `arrival_radius` (config, default 0.05 units) of its position.
  - Choosing a job at another position makes the plan two steps: `walk` to the position, then the job. The decision system records the plan (REQ-4.1.2); on arrival the job starts.
  - A job is chosen only if the NPC has enough stamina for the walk plus at least one tick of work (REQ-4.1.7 and 4.1.10 apply as before).
  - After a shift the NPC stays where it is. The next decision starts from its current position, so it may continue at the same site without walking.
  - Selling or buying goods still goes through the market of the city the NPC is in, as before.
- **REQ-3A.7 Commute in job choice:** the walk costs stamina but does not count as working time.
  - **Income per stamina** (REQ-4.1.7) is computed over the whole task, with the walk counted both ways: net income per stamina = (income from a shift) / (stamina of the shift + stamina of the walk to the job + stamina of the walk back to where the NPC started). A job further away is therefore worth less, all else equal; a distant node pays only if its price or skill makes up for the round trip.
  - **Income per time** counts only the hours spent at the job's position. The hours of the walk are not added to the task, so the hourly income of a job is the same whether the NPC lives next to it or far away. This hourly income is what the reservation wage comparison (REQ-4.1.5) uses.
  - Self-supply (gather a need good yourself) uses the same stamina round trip as income per stamina.
  - The return leg is part of how the job is valued. The NPC actually walks only when a decision calls for it (to a job, home to sleep, to another job), so after a shift it stays where it is until its next decision.
- **REQ-3A.8 Houses and home:** each house has a `Position`, a city, an owner and an occupant (REQ-3.7). With `housing_pct` below 100, some NPCs have no house; with more than 100, some houses have no occupant.
  - An NPC's home is a house, so `Home` refers to a house entity. An NPC without a house has the city center as its home position.
  - **Better rest at home:** while sleeping at its house, an NPC restores stamina at `sleep_restore_per_hour * movement.home_rest_multiplier` (config, default 1.25). Sleeping anywhere else restores the base `sleep_restore_per_hour`. The existing penalty for a critical shelter need (REQ-4.2.1) still applies on top.
  - **Walking home:** an NPC that owns or occupies a house walks home to sleep, unless it is already within `arrival_radius`. The decision weighs the extra stamina the home bonus gives over the planned sleep against the stamina of the walk, so it sleeps where it is when the house is too far to be worth it.
  - An NPC without a house sleeps where it stands and gets the base rate. A scenario with no houses therefore behaves as before (REQ-3A.9).
  - House position does not change prices or rent in this version; price and installments stay as in REQ-3.7.
- **REQ-3A.9 Determinism and compatibility:**
  - Distances use IEEE floating point and `math.hypot`. Derived values (road length, hours, stamina cost) are computed once, when the road or event is created, and stored.
  - Generated positions (REQ-3A.1) use their own RNG stream, seeded from the scenario seed and a fixed constant, never the main simulation RNG. A scenario without any coordinates, workplaces or houses therefore gives results identical to the previous version, for the same seed.
  - Cities either all have coordinates or none do. Validation rejects a mix.
  - **One speed for everyone:** walking and road speed and stamina rates (REQ-3A.3) are global. They do not depend on age, sickness, load or the individual NPC, and a road has no quality of its own beyond its length and any explicit override (REQ-3A.2).
- **REQ-3A.11 Resource nodes on the overworld:** a resource node is not in a city. It lies outside every city radius and is reached over a **branch road**: a minor road that leaves a main road (a road between two cities) at a junction.
  - **Definition:** a node gives its `at: [x, y]` and `branch: {road, t}`, where `road` is the main road and `t` (0 to 1) is how far along it the junction lies, measured from the road's first city. The junction is the point at fraction `t` of the straight line between the two city centers. The node can be given in part, and the builder fills in the rest deterministically (REQ-3A.9): with `at` but no `branch`, the junction is the foot of the perpendicular from the node to the nearest road, clamped to the road, with ties going to the road listed first; with `branch` but no `at`, the node is placed at a fixed offset beside the junction; with neither, validation rejects the node. A node inside a city radius, a `t` outside 0-1, or an unknown road is rejected by validation.
  - **Branch length:** `branch_length` defaults to the straight-line distance from junction to node and can be set higher for a winding branch, never lower (as in REQ-3A.2). Its `hours` and `stamina_cost` follow the road rates (REQ-3A.3) and can be overridden in the same way.
  - **Trip:** to gather, an NPC goes from its city center along the main road to the junction (`t * length` of the road, or `(1 - t) * length` from the other end, with `hours` and `stamina_cost` in proportion), then along the branch to the node, works there, and returns the same way. Both end cities of the main road can use the node; other cities reach it only through the roads between them, which this version does not plan for (see Open Questions).
  - **Valuation:** the whole round trip, main road plus branch, counts in income per stamina and not in income per hour, as in REQ-3A.7. Self-supply uses the same round trip.
  - **Away from a city:** while on the road or at the node, `Location.city` is none (REQ-3A.1). The NPC cannot place or fill orders until it returns to a city (this replaces the market access of REQ-3A.5 for those hours). Goods gathered are sold at the market of the city where the NPC arrives.
  - **Roads can change:** if events remove the main road (REQ-3A.2), its nodes keep existing but cannot be reached until a road is added again between the same cities. Their stock still regenerates.
  - **Builder rule:** the scenario lists them under a top-level `nodes:`. A scenario with such nodes must be spatial (cities with coordinates, REQ-3A.9) and every node must end up on a road (its own `branch`, or the nearest road). A city with no road has no access to resources. The per-city `nodes:` of older scenarios (resource nodes inside a city, up to 200 per city) still load and behave as before, so scenarios without coordinates keep their results; the Map tab and its helpers create only overworld nodes (up to 500 per world).
- **REQ-3A.10 Output:** snapshots and `config.json` store the positions, the generated houses and each node's branch (road, `t`, length). The daily city table adds `walk_hours` and `walk_stamina` (totals for the city's residents), and each road row in the run output includes its `length`.

## 4. Systems (Logic)

- **REQ-4.1 Physiology System:**
  - Drains satiety based on activity.
  - Recalculates `daily_max_stamina` from sleep hours.
  - Rolls RNG for sickness when the entity is overworked.
- **REQ-4.2 Decision (AI) System:** evaluates NPC needs. See section 4.1 for the decision model.
  - Hungry: check inventory, then wallet; if money exists and there is no food, place a Buy Order.
  - Broke: place a Labor Offer on the Job Board, or accept an existing Job Order.
- **REQ-4.3 Task/Job System:** matches NPCs to tasks. NPCs gravitate to jobs with the highest profit margin per unit of stamina (e.g. high mining skill leads to mining). A task has a position and the NPC must be there to perform it (REQ-3A.6).
- **REQ-4.4 Market Clearing System:** the core of emergence.
  - Use a Continuous Double Auction / order book.
  - NPCs post bids and asks; a match produces a transaction.
  - The clearing price becomes the new market price.
  - Orders from the player (REQ-1C.9) are queued and sent in the next tick's batch, then use the same order book, priority and settlement as NPC orders.
- **REQ-4.5 Merchant AI:** Merchant NPCs buy where the price is low, travel, and sell where it is high.

### 4.1 NPC Decision Model

- **REQ-4.1.1 Architecture:** hierarchical needs, with softer priorities than strict Maslow ordering.
  - Each NPC has randomized attributes that make it favor some needs over others, be stricter or looser about thresholds, and lean more or less toward luxury.
  - Each NPC has a "needs" table with thresholds derived from these traits.
  - No genetic code, genetic algorithm or inheritance is needed. Traits are randomized per NPC.
- **REQ-4.1.2 Decision frequency:** every tick. An NPC commits to an action until it finishes, unless an urgent need interrupts it.
- **REQ-4.1.3 Urgency:** continuous nonlinear urgency curves, rising sharply as a need nears critical.
- **REQ-4.1.4 Actions:** gather, work a job, buy, sell, eat/consume, rest/sleep, travel, seek care. Walking to a position is an action (REQ-3A.5).
- **REQ-4.1.5 Pricing:** reservation price comes from marginal utility and urgency. It is adjusted by recently observed market prices and the NPC's risk/urgency tolerance.
- **REQ-4.1.6 Information:** local only. An NPC knows its current city's prices plus remembered, possibly stale prices from places it has visited.
- **REQ-4.1.7 Job choice:** based mainly on expected income per unit of stamina. It also includes a smaller weight for personal preference and a smaller weight for personal skill in that job. The commute counts against a job's income per stamina (REQ-3A.7).
- **REQ-4.1.8 Planning:** NPCs plan ahead by forecasting prices and planning multi-day strategies (saving, stockpiling, anticipating shortages).
  - Forecast: exponential moving average (EMA) of observed prices, per good and per city.
  - Horizon: short, about 3-7 days. It is trait-dependent, and thrifty NPCs plan longer.
  - Forecasts vary per NPC: a trait-driven confidence/noise factor makes some NPCs over- or under-react.
- **REQ-4.1.9 Traits:** fixed per-NPC traits (risk, urgency, thrift, luxury propensity) randomized at birth. They are not inherited.
- **REQ-4.1.10 Conflict resolution:** priority order is survival, then health, then wealth, then luxury.
  - The order is strict only when a need is at its critical threshold.
  - Below the critical threshold, NPCs weigh needs by their traits (REQ-4.1.1).

### 4.2 Needs Table

- **REQ-4.2.1 Tiers and needs:**
  - Survival: food+water (one good), rest/stamina, shelter/safety, satisfied by housing (REQ-3.7).
  - Health: medicine/care.
  - Wealth: savings target = X days of expected spending, scaled by the thrift trait. The need rises with unmet future needs and pending payments.
  - Luxury: one luxury good.
- **REQ-4.2.2 Representation:** each need is a level in 0-1. It decays over time or activity and is refilled by consuming its good.
- **REQ-4.2.3 Thresholds:** each NPC has comfort, urgent and critical levels per need. They are sampled around a global default, with trait-based variation.
- **REQ-4.2.4 Randomization:** Gaussian around the defaults with a configurable spread, clamped to valid ranges. This applies to both traits and thresholds.
- **REQ-4.2.5 Failure effects:**
  - Death occurs if a survival need stays at zero for N ticks.
  - A failing health need raises sickness and death risk.
- **REQ-4.2.6 Configuration:** the needs table, defaults, decay rates and trait spreads live in a config file (YAML/JSON), not in code.

## 5. Emergent Economics Constraints

- **REQ-5.1** Strictly conserve resources and currency. Money is created or destroyed only by a central authority (the simulation controller).
  - Because the money supply is fixed (REQ-2.2.1), a stimulus is a scenario event that transfers gold from a treasury to wallets.
  - The player account (REQ-1C.9) is the other central-authority injection: its opening grant and any top-up the user makes during the run are minted, logged as such and counted in the conservation checks.
- **REQ-5.5 Taxes and spending:** a simple tax and public-spending system moves money between wallets and treasuries. Treasuries are conserved accounts.
  - Treasuries are per city, each with its own tax rates, and each city collects and spends locally.
  - The initial treasury share is a configurable fraction of total gold (e.g. 10-20%).
  - Taxes: a flat income tax on wages and sales, plus an optional per-transaction sales tax.
  - Spending: wages for public jobs (e.g. road building, guards), plus a basic support payment to NPCs below survival thresholds.
  - Rates and policy are fixed per scenario, with no feedback from NPCs or metrics.
- **REQ-5.2 Inflation scenario:** support injecting money into NPC wallets (stimulus).
  - With fixed physical goods, urgency tolerance leads to higher bids, sellers raise asks, and prices trend upward.
- **REQ-5.3 Arbitrage scenario:**
  - City A is forested (cheap wood); City B is a desert (expensive wood).
  - Connecting them (e.g. building a road) lets merchants raise A's prices and lower B's.
  - Prices should converge toward equilibrium.
- **REQ-5.4** Price, inflation, and trade-flow phenomena must emerge from agent behavior, not be hard-coded.

## 6. Suggested Acceptance Checks

- Total currency and total resources stay constant, apart from explicit central-authority injections and from regeneration or consumption.
- A stimulus raises the average market price over time.
- Connecting isolated cities narrows the price gap between them.
- Sleep deprivation lowers `daily_max_stamina`, which raises sickness probability.
- Higher skill lowers stamina cost per unit gathered.

## 7. Open Questions

- Numeric defaults: decay rates, threshold values, trait spreads, EMA smoothing factor, and N ticks to death.
- Housing details: house price and decay, rent versus ownership, interest on debt, default and foreclosure rules, and inheritance rules.
- Recipes and labor: which goods need which inputs and how much work.
- Tax and support numbers: rates, support payment size, and public job wages.
- Parallelism (REQ-1A.3): which systems are safe to parallelize, and how to keep results deterministic (e.g. fixed merge order).
- Fixed-point precision for goods quantities and the gold unit size.
- Snapshot naming that pairs a world save with its API save.
- Inspector (REQ-1C.5, REQ-1C.6): the "spending plan" is not a stored object today, only derived values (stock targets, reservation prices, EMA forecasts, savings target). Decide whether showing derived values is enough or whether NPCs should hold an explicit multi-day plan (REQ-4.1.8).
- Inspector: jobs are gathering tasks and public jobs paid by the city treasury (REQ-5.5). There are no job contracts yet, so the contracts part of the jobs section stays empty until REQ-1A.8 is built.
- Overworld nodes: how a city reaches nodes on roads that are not adjacent to it (route planning over several roads), whether a branch may leave from another branch, and whether isolated cities (REQ-3.4) should be allowed a direct branch from the city center.
- Overworld nodes: how existing scenarios, which put nodes inside cities, are migrated (a one-off script that moves each node to a branch on the city's nearest road is the proposal).
- Housing: whether `housing_pct` should apply to a population that changes during a run, if births and arrivals are added later.
- Space: whether cities should have an area that can hold only so many nodes and houses, and the default distance unit and city sizes for new scenarios.
- Space: crowding at a resource node (several NPCs on one node) stays as today (shared stock, no queue); a node's position does not limit how many can work there.
- Inspector: buffer sizes, how long dead NPCs are kept, and whether history should also be exported to CSV for finished runs (more disk per run).
- Inspector: whether a finished run without a snapshot should be inspectable from the run files alone (citizen-level history is not in the current CSVs).
- Map editor (REQ-1C.8): whether an area should be a real object in the scenario (for example a forest with a radius that the simulation fills with nodes at run time) rather than a way to author individual nodes; this draft keeps points in the model.
- Map editor: whether "automatic" should also be available for city positions at run time (the builder arranging cities itself) instead of only through the Arrange cities helper, which writes explicit coordinates.
- Map editor: the limits (50 cities, 500 nodes per world) and the default richness values for scattered resources.
- Market tab: the default opening balance (`gold_share` 0.5 of the NPCs' gold, no goods) and whether top-ups should have a ceiling; this draft sets none, because the tab exists to move the market, but a very large injection will inflate prices (REQ-5.2).
- Market tab: whether a world with a player should keep a record of how the player's trades moved the NPCs' prices (impact), and whether the player's account should show in the run's summary table.
