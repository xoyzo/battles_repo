# battles

An interactive, prediction-based PvP battle system for BallsDex, played entirely through Discord buttons — Duel, Free For All, and Team Battle out of the box, plus dashboard-authored custom modes and abilities.

This is a **BallsDex package** — a Django app (for models/admin) plus a `package/` sub-folder (the discord.py extension) — meant to be installed into an existing BallsDex bot, not run standalone.

## Design principles (why it's built this way)

- **No parallel game data.** Ball ownership, collections, rarity, Regime, and Economy all come straight from `bd_models`. This app doesn't duplicate any of that — see `package/integrations/`.
- **Ball stats aren't a separate table.** `package/integrations/balls.py` derives HP/Attack/Defense/Speed from whatever stat-like fields the host's `bd_models.Ball` already exposes (`health`/`attack`), rather than a dedicated stat model. If your fork names these fields differently, that's the one place to adjust.
- **Modes, not hardcoded game types.** Duel / Free For All / Team Battle are ordinary `BattleMode` rows (seeded once, fully editable) — creating a new mode is a dashboard form, never a code change.
- **Abilities are dashboard Python, sandboxed.** No declarative-JSON mini-language and no hardcoded ability classes — admins write real Python against a restricted `AbilityContext` API (see `package/ability_sandbox.py`).
- **Economy is a classification, not currency.** `package/integrations/economy.py` mirrors `regimes.py` — Capitalism/Socialism/Mixed Economy style Ball tags abilities and battle rules can key off. Actual currency payouts live in `package/integrations/currency.py`.

## Install

1. Drop this repo's `battles/` folder alongside your bot's other installed packages.
2. Run migrations: `python manage.py migrate battles`.
3. Load the package the same way you load any other BallsDex package (`apps.py`'s `dpy_package = "battles.package"`).
4. On first ready, the package seeds the three built-in modes (Duel, Free For All, Team Battle) if they don't already exist — safe to run repeatedly, never overwrites your edits to them.
5. Configure everything from Django admin (`/admin/battles/`) — modes, abilities, global formula tuning, and base reward amounts are all registered there. `battles/package/dashboard/` additionally exposes purpose-built single-page views (including the Ability code editor) if you want to mount `/battling/` as its own dashboard section.

## How a battle plays out

- **Direct challenge** (2-player modes): `/battle start opponent:@User mode:Duel ball_id:<id>` sends a public Accept/Decline prompt; on accept, the opponent picks their own ball and the battle starts immediately.
- **Lobby** (3+ player modes, or any mode played without a specific opponent): `/battle start mode:"Chaos Arena" ball_id:<id>` opens a Join/Leave/Start lobby. Other players click Join, pick a ball, and the host starts once the mode's minimum player count is met. Team Battle auto-splits joiners into teams.
- Each turn, every alive participant privately (ephemeral) chooses one of ⚔️ Attack, 🛡️ Defend, 🔄 Counter, 💚 Heal, 💨 Dodge, ✨ Ability — Attack additionally prompts for a target when more than one enemy is available (Free For All / Team Battle; a Duel's target is implicit). Neither player can see another's pick until everyone has locked in, or the turn timer runs out (behavior on timeout — Auto Defend / Skip Turn / Random Action — is configured per mode).
- Turns resolve, the embed updates, and play continues up to the mode's max turns, or until only one participant/team is left standing.
- On finish, currency rewards (win/loss/draw, scaled by the mode's multipliers, plus streak bonuses) are paid out through whatever currency cog is installed, and a Rematch button appears if the mode allows it.

## Package layout

```
battles/
├── models.py            BattleConfig, BattleMode, Ability, BattleReward, Battle, BattleParticipant, BattleTurn
├── admin.py               Full Django admin CRUD for every model above
├── migrations/
└── package/
    ├── cog.py              Slash commands + battle/lobby lifecycle orchestration
    ├── views.py             ChallengeView, LobbyView, BattleView (N-participant action collection), Ability/Target choice views
    ├── buttons.py            Discord Button/Select components
    ├── embeds.py             Lobby / N-participant battle / result embed rendering
    ├── engine.py              Turn resolution generalized to any number of participants with targeting
    ├── actions.py              Action definitions (label, emoji, cooldown/use-limit wiring)
    ├── formulas.py             Damage formula, crits, momentum modifiers — snapshot-driven
    ├── modes.py                 Built-in mode seeding, mode selection, deck validation, snapshot building
    ├── abilities.py              Ability eligibility checks (species/rarity/regime/economy/mode) + hook dispatch
    ├── ability_api.py             AbilityContext: damage()/heal()/modify_stat()/add_effect()/change_momentum()/give_currency() + teammates/enemies/economy/regime helpers
    ├── ability_sandbox.py          Restricted Python execution for dashboard-authored ability scripts
    ├── helpers.py                  HP/momentum bars, participant lookups, misc formatting
    ├── monkeypatch.py               Safe, idempotent BallInstance extensions (applied post-ready)
    ├── tasks.py                      Background sweep that expires inactive battles
    ├── config.py                      Async accessors for the global BattleConfig/BattleReward
    ├── dashboard/                      /battling views: settings, modes, abilities (code editor), rewards
    └── integrations/
        ├── balls.py                    Species/instance stat + display helpers (no parallel stat table)
        ├── currency.py                  Currency payouts (Money/Currency/Wallet/Bank cog, tried in order)
        ├── economy.py                    Ball Economy *classification* helpers (not currency)
        ├── regimes.py                     Regime → damage/defense/healing/reward/cooldown modifiers
        └── events.py                       before_battle / before_turn / after_turn / after_battle hook registry (admin Events, e.g. Double Damage / Legendary Only Arena)
```

## Extending

- **New mode**: create a `BattleMode` row from the dashboard — no code. It appears in the mode selector immediately, alongside the built-ins.
- **New ability**: write Python in the Ability editor. Define `execute(ctx)` for the active Ability button, or any of the passive hooks — `battle_start`, `before_turn`, `after_turn`, `before_action`/`after_action`, `on_attack`, `on_defend`, `on_damage_taken`, `on_win`, `on_loss` — for something that triggers automatically. `ctx` exposes `damage()`, `heal()`, `modify_stat()`, `add_effect()`, `change_momentum()`, `give_currency()`, plus read-only `get_teammates()` / `get_enemies()` / `self_economy` / `self_regime`. Scripts run through `ability_sandbox.py`: no imports, no dunder access, no `exec`/`eval`/`open`, and a hard wall-clock timeout — never the real interpreter.
- **New action**: add an `ActionKey` + `ActionDefinition` in `actions.py`, add its button, and add its interaction rule to `engine.py`'s `_resolve_attacker_vs`.
- **New event**: register a callback against `before_battle` / `before_turn` / `after_turn` / `after_battle` with `integrations.events.on(...)`.
- **Economy/Regime-driven mechanics**: `integrations/economy.py` and `integrations/regimes.py` both expose modifier-resolution helpers abilities and battle rules can call — team-same-Economy bonuses, Economy-vs-Economy damage matchups, Regime-driven damage/healing multipliers, etc.

## Notes / known simplifications

- Discord doesn't support disabling a shared message's buttons for only one of several viewers. "Others can't see your choice" is implemented via ephemeral acknowledgements and by ignoring a second click from a participant who already locked in, rather than visually disabling the public message's buttons per-player.
- Active Ability actions resolve outside the core Attack/Defend/Counter/Heal/Dodge interaction matrix (they can't currently be blocked/countered/dodged), since an ability's actual behavior is opaque dashboard-authored script rather than a fixed formula the matrix can reason about.
- The original Duel-only "Defend is weak against Counter" chip-damage flavor rule doesn't generalize cleanly to independent per-participant targets in Free For All / Team Battle, so it isn't part of the general resolver — a natural rule to layer back in specifically for `mode_type == "duel"` if you want it.
- `ctx.modify_stat()` stat buffs apply for the triggering moment; persisting a multi-turn buff/debuff (with decay) is a clean extension: store it in `Battle.state["effects"]` with a remaining-duration counter and fold active ones into a participant's attack/defense before each turn resolves.
- Win-streak bonuses are computed by walking a player's most recent finished `BattleParticipant` rows rather than a running counter — fine at normal volumes, swap in a dedicated counter field if you expect heavy battle traffic.
- Rematch re-launches with the same players/balls in the same mode; it doesn't currently re-run the original lobby's join flow for a different roster.
