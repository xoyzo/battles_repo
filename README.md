# battles

An interactive, prediction-based PvP battle system for BallsDex, played entirely through Discord buttons.

This is a **BallsDex package** — a Django app (for models/admin) plus a `package/` sub-folder (the discord.py extension) — meant to be installed into an existing BallsDex bot, not run standalone.

## Install

1. Drop this repo's `battles/` folder alongside your bot's other installed packages (or install via your normal GitHub-release package flow).
2. Run migrations: `python manage.py migrate battles`.
3. Load the package the same way you load any other BallsDex package (it registers itself via `apps.py`'s `dpy_package = "battles.package"`).
4. Configure timers, formulas, abilities, ball stats, and rewards from the Django admin (`/admin/battles/`) — every model is registered there. `battles/package/dashboard/` additionally exposes a couple of purpose-built single-page views if you want to mount `/battling/` as its own section rather than using the generic admin.

## How a battle plays out

1. `/battle challenge @opponent ball_id:<id>` — sends a public Accept/Decline prompt.
2. On accept, the opponent picks their own ball, and the battle starts: a public embed shows both balls' HP, momentum, and status effects.
3. Each turn, both players privately (ephemeral) choose one of ⚔️ Attack, 🛡️ Defend, 🔄 Counter, 💚 Heal, 💨 Dodge, ✨ Ability. Neither player can see the other's pick until both have locked in (or the turn timer runs out, at which point a non-responding player auto-Defends and loses momentum).
4. Once both actions are in, the turn resolves, the embed updates with the outcome, and the next turn begins — up to the configured max turns, or until a ball's HP hits zero.
5. On finish, currency rewards (win/loss/draw/streak) are paid out through whatever economy integration is installed.

## Package layout

```
battles/
├── models.py            Battle, BattleTurn, Ability, BallBattleStats, BattleConfig, BattleReward
├── admin.py              Full Django admin CRUD for every model above
├── migrations/
└── package/
    ├── cog.py             Slash commands + battle lifecycle orchestration
    ├── views.py            ChallengeView, BattleView (per-turn action collection), AbilityChoiceView
    ├── buttons.py          Discord Button/Select components
    ├── embeds.py           Battle Arena embed rendering
    ├── engine.py            Turn resolution: the Attack/Defend/Counter/Heal/Dodge interaction matrix
    ├── actions.py            Action definitions (label, emoji, cooldown/use-limit wiring)
    ├── formulas.py           Damage formula, crits, momentum modifiers — all snapshot-driven
    ├── abilities.py           Ability eligibility checks + declarative effect interpreter
    ├── ability_api.py          AbilityContext: damage()/heal()/modify_stat()/add_effect()/change_momentum()/give_currency()
    ├── helpers.py             HP bars, momentum bars, misc formatting
    ├── monkeypatch.py         Safe, idempotent BallInstance extensions (applied post-ready)
    ├── tasks.py                Background sweep that expires inactive battles
    ├── config.py               Async accessors for the active BattleConfig/BattleReward
    ├── dashboard/               Optional standalone /battling views (settings, abilities, ball stats, rewards)
    └── integrations/
        ├── balls.py             Species/instance stat + display helpers
        ├── economy.py           Currency payouts (custom Economy cog first, core money cog fallback)
        ├── regimes.py            Regime → damage/defense/healing/reward/cooldown modifiers
        └── events.py              before_battle / before_turn / after_turn / after_battle hook registry
```

## Extending

- **New action**: add an `ActionKey` + `ActionDefinition` in `actions.py`, add its button, and add its interaction rules to `engine.py`.
- **New ability**: no code needed for the common cases — create it from the dashboard with a declarative `effect` JSON (`buff_attack`, `damage`, `heal`, `momentum_shift`, `shield`, `dot`, `currency`, or `composite`). For bespoke logic, register a Python handler with `@ability_api.register_custom_ability("your_key")` and reference `your_key` from the ability's `script` field.
- **New event**: register a callback against `before_battle` / `before_turn` / `after_turn` / `after_battle` with `integrations.events.on(...)`.
- **New regime behaviour**: extend `integrations/regimes.py`'s modifier resolution (or add a `battle_modifiers` JSON field to your `Regime` model — it's picked up automatically if present).

## Notes / known simplifications

- Discord doesn't support disabling a shared message's buttons for only one of two viewers. "The opponent can't see your choice" is implemented via ephemeral acknowledgements and by ignoring a second click from a player who already locked in, rather than visually disabling the public message's buttons per-player.
- Abilities resolve outside the core Attack/Defend/Counter/Heal/Dodge interaction matrix (they can't currently be blocked/countered/dodged) to keep the ability API simple and safely sandboxed; this is a natural place to extend if you want abilities to interact with the matrix.
- Win-streak bonuses are computed by walking a player's most recent finished battles rather than storing a running counter — fine at normal volumes, but swap in a dedicated counter field if you expect heavy battle traffic.
- Ability `modify_stat` effects apply for the turn the ability is used; persisting multi-turn buffs/debuffs is a straightforward extension (store the modifier + remaining duration in `Battle.state["effects"]`, fold active ones into attack/defense before each turn resolves, decrement duration per turn) but isn't wired up yet.
