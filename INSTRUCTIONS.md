Building a society simulation with emergent macroeconomics requires a bottom-up Agent-Based Modeling (ABM) approach. To mimic Unity’s architecture in Python, you should use an Entity-Component-System (ECS) design.
In ECS, entities (NPCs, cities, resource nodes) are just IDs. Components are pure data (Inventory, Health, Location). Systems contain the logic (MarketSystem, PhysiologySystem) and iterate over entities with specific components. You can build this from scratch or use a lightweight Python ECS library like esper.
Here is a brainstormed blueprint for your simulation architecture and economic mechanics.
1.⁠ ⁠Component Brainstorming (The Data)
To achieve emergent behavior, NPCs need attributes that tie their physical limitations directly to their economic decisions.
Biological & Physiological Components:
 * Stamina: current_stamina, daily_max_stamina, absolute_max_stamina. The "spring elasticity" mechanic means if sleep is skipped, daily_max_stamina drops. It recovers slowly over consecutive days of good rest.
 * Metabolism: satiety_level, caloric_burn_rate. Moving or working increases the burn rate.
 * Health: sickness_probability, immune_strength. Drops when daily_max_stamina is low. Sickness reduces work efficiency and increases resting needs.
 * Demographics: Age, gender, life expectancy.
Economic Components:
 * Wallet: Fiat currency or a base commodity (like gold) used as the medium of exchange.
 * Inventory: A dictionary mapping resource IDs to quantities (e.g., {wood: 10, food: 2}).
 * Skills/Proficiencies: Floating-point multipliers for tasks (e.g., Mining: 1.2, Farming: 0.8). Higher skill means less stamina drained per unit of resource gathered.
 * Risk/Urgency Tolerance: A psychological float determining how willing an NPC is to overpay for food when starving, or undercut competitors when desperate for work.
2.⁠ ⁠City & World Architecture
Cities and resource nodes are also entities with components.
 * Resource Nodes: Possess a YieldComponent (amount of wood/stone), a RegenerationRate, and a Location.
 * Cities: Act as economic hubs. They possess a MarketBoard (for buying/selling goods) and a JobBoard (for labor contracts).
3.⁠ ⁠Core Systems (The Logic Loop)
During each tick (or hour/day) of the simulation, systems process the entities.
 * Physiology System: Drains satiety based on activity. Recalculates daily_max_stamina based on sleep hours. Rolls RNG for sickness if the entity is overworked.
 * Decision (AI) System: Evaluates an NPC's needs.
   * If hungry: Check inventory. If no food, check wallet. If money exists, place a Buy Order on the Market Board.
   * If broke: Place a Labor Offer on the Job Board, or accept an existing Job Order from a producer.
 * Task/Job System: Matches NPCs to tasks. An entity with a high mining skill will naturally gravitate toward mining jobs because they calculate a higher profit margin per unit of stamina spent.
 * Market Clearing System (The Engine of Emergence): This is the most critical piece. Use a Continuous Double Auction (CDA) or an order book. NPCs post bids (willing to buy at X) and asks (willing to sell atY). When a bid matches an ask, a transaction occurs. The clearing price becomes the new "market price."
4.⁠ ⁠Designing for Emergent Economics
To ensure phenomena like inflation and trade shifts naturally emerge, you must strictly enforce conservation of resources and currency. Money cannot be created or destroyed except by a central authority (your simulation controls).
 * Triggering Inflation: Inject money directly into NPC wallets (a "stimulus"). Because NPCs have more money but the physical amount of food/wood in the world hasn't changed, their Risk/Urgency Tolerance allows them to place higher bids on the market. Sellers realize they can raise their asks. The market price naturally trends upward.
 * Connecting Cities (Arbitrage): Keep City A and City B mathematically isolated at first. City A is surrounded by forests (cheap wood). City B is in a desert (expensive wood).
   * Introduce "Merchant" NPCs whose AI logic is simply: Buy where price is low, travel, sell where price is high.
   * When you connect the cities (e.g., build a road that lowers travel stamina cost), merchants will buy wood in A, decreasing supply and raising A's prices, and sell it in B, increasing supply and lowering B's prices. The economies will naturally seek an equilibrium.
