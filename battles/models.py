"""Django models backing the battles package.

Refactor note: battle-stat data is intentionally *not* duplicated here.
Ball ownership, collections, rarity, regimes, and economies all live in
`bd_models` and are used directly (see `package/integrations/balls.py`,
`regimes.py`, `economy.py`). This app only stores what's genuinely
battle-specific: live battle/participant state, turn history, dashboard-
authored modes and abilities, and reward multipliers.

Conventions followed:
- `Self` + `Manager[Self]` typed managers.
- String-based FK references to avoid import cycles with `bd_models`.
- Validation lives in `clean()`, not field validators.
- No `auto_now=True` — timestamps are set manually where mutated.
"""
from __future__ import annotations

from typing import Self

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Manager


class BattleConfig(models.Model):
    """Global damage-formula tuning, independent of any particular mode.

    Modes (see `BattleMode`) configure *what* a battle looks like (players,
    deck, turn timers, enabled actions, reward multipliers); this table
    configures the underlying math every mode shares.
    """

    objects: Manager[Self] = Manager()

    name = models.CharField(
        max_length=64, default="default", unique=True,
        help_text="Internal identifier for this configuration profile.",
        verbose_name="configuration name",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this configuration profile is currently in use.",
        verbose_name="active",
    )

    attack_multiplier = models.FloatField(
        default=1.0,
        help_text="Multiplier applied to attacker's Attack stat in the damage formula.",
        verbose_name="attack multiplier",
    )
    defense_multiplier = models.FloatField(
        default=1.0,
        help_text="Multiplier applied to defender's Defense stat in the damage formula.",
        verbose_name="defense multiplier",
    )
    minimum_damage = models.PositiveIntegerField(
        default=5,
        help_text="Damage floor applied after all reductions.",
        verbose_name="minimum damage",
    )
    critical_hit_chance = models.FloatField(
        default=0.10,
        help_text="Probability (0-1) that an attack lands as a critical hit.",
        verbose_name="critical hit chance",
    )
    critical_hit_multiplier = models.FloatField(
        default=1.5,
        help_text="Damage multiplier applied on a critical hit.",
        verbose_name="critical hit multiplier",
    )
    defend_damage_reduction = models.FloatField(
        default=0.5,
        help_text="Fraction of incoming damage negated while defending.",
        verbose_name="defend damage reduction",
    )
    counter_cooldown_turns = models.PositiveIntegerField(
        default=2,
        help_text="Turns a player must wait before using Counter again.",
        verbose_name="counter cooldown (turns)",
    )
    counter_reflect_multiplier = models.FloatField(
        default=1.0,
        help_text="Fraction of negated attack damage reflected back to the attacker.",
        verbose_name="counter reflect multiplier",
    )
    dodge_cooldown_turns = models.PositiveIntegerField(
        default=2,
        help_text="Turns a player must wait before using Dodge again.",
        verbose_name="dodge cooldown (turns)",
    )
    heal_uses_per_battle = models.PositiveIntegerField(
        default=3,
        help_text="Maximum number of times a player may Heal in a single battle.",
        verbose_name="heal uses per battle",
    )
    heal_amount_fraction = models.FloatField(
        default=0.25,
        help_text="Fraction of max HP restored by a single Heal action.",
        verbose_name="heal amount (fraction of max HP)",
    )
    momentum_max = models.IntegerField(
        default=5, help_text="Upper bound of the momentum meter.", verbose_name="momentum maximum",
    )
    momentum_min = models.IntegerField(
        default=-5, help_text="Lower bound of the momentum meter.", verbose_name="momentum minimum",
    )
    momentum_high_threshold = models.IntegerField(
        default=3,
        help_text="Momentum value at which the positive damage bonus applies.",
        verbose_name="momentum high threshold",
    )
    momentum_high_damage_bonus = models.FloatField(
        default=0.10,
        help_text="Damage bonus fraction applied at or above the high momentum threshold.",
        verbose_name="momentum high damage bonus",
    )
    momentum_crit_threshold = models.IntegerField(
        default=5,
        help_text="Momentum value at which the critical chance bonus applies.",
        verbose_name="momentum critical threshold",
    )
    momentum_crit_bonus = models.FloatField(
        default=0.20,
        help_text="Bonus fraction added to critical hit chance at the momentum crit threshold.",
        verbose_name="momentum critical bonus",
    )
    momentum_low_threshold = models.IntegerField(
        default=-3,
        help_text="Momentum value at or below which the negative damage penalty applies.",
        verbose_name="momentum low threshold",
    )
    momentum_low_damage_penalty = models.FloatField(
        default=0.10,
        help_text="Damage penalty fraction applied at or below the low momentum threshold.",
        verbose_name="momentum low damage penalty",
    )
    afk_momentum_penalty = models.IntegerField(
        default=1,
        help_text="Momentum lost when a player fails to act before the turn timer expires.",
        verbose_name="AFK momentum penalty",
    )
    updated_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp this configuration was last saved.", verbose_name="updated at",
    )

    class Meta:
        verbose_name = "battle configuration"
        verbose_name_plural = "battle configurations"

    def __str__(self) -> str:
        return f"BattleConfig({self.name})"

    def clean(self) -> None:
        if self.momentum_min >= self.momentum_max:
            raise ValidationError("momentum_min must be less than momentum_max.")
        if not (0.0 <= self.critical_hit_chance <= 1.0):
            raise ValidationError("critical_hit_chance must be between 0 and 1.")
        if not (0.0 <= self.defend_damage_reduction <= 1.0):
            raise ValidationError("defend_damage_reduction must be between 0 and 1.")


class BattleMode(models.Model):
    """A dashboard-authored battle preset — "PvP kit" in Minecraft-server
    terms. Duel, Free For All, and Team Battle ship as ordinary rows
    (`is_builtin=True`, seeded on first run) rather than hardcoded Python
    classes, so admins can create as many custom presets as they like from
    the dashboard alongside them.
    """

    class ModeType(models.TextChoices):
        DUEL = "duel", "Duel"
        FREE_FOR_ALL = "ffa", "Free For All"
        TEAM = "team", "Team Battle"

    class TimeoutBehavior(models.TextChoices):
        AUTO_DEFEND = "auto_defend", "Auto Defend"
        SKIP_TURN = "skip_turn", "Skip Turn"
        RANDOM_ACTION = "random_action", "Random Action"

    objects: Manager[Self] = Manager()

    # -- General ---------------------------------------------------------
    name = models.CharField(
        max_length=64, unique=True,
        help_text="Display name shown in the mode selector (e.g. 'Duel', 'Chaos Arena').",
        verbose_name="name",
    )
    description = models.TextField(
        blank=True, default="",
        help_text="Player-facing description of this mode.", verbose_name="description",
    )
    icon = models.CharField(
        max_length=100, blank=True, default="⚔️",
        help_text="Emoji or short icon token shown next to the mode name.", verbose_name="icon",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Whether this mode currently appears in the mode selector.", verbose_name="enabled",
    )
    is_builtin = models.BooleanField(
        default=False,
        help_text="Marks Duel / Free For All / Team Battle. Built-in modes can still be fully edited from the dashboard, just not deleted.",
        verbose_name="built-in",
    )
    mode_type = models.CharField(
        max_length=8, choices=ModeType.choices, default=ModeType.DUEL,
        help_text="Underlying pairing rules the engine uses: head-to-head, every-participant-for-themselves, or team-based.",
        verbose_name="mode type",
    )

    # -- Players -----------------------------------------------------------
    min_players = models.PositiveIntegerField(
        default=2, help_text="Minimum number of players required to start.", verbose_name="minimum players",
    )
    max_players = models.PositiveIntegerField(
        default=2, help_text="Maximum number of players allowed to join.", verbose_name="maximum players",
    )
    teams_enabled = models.BooleanField(
        default=False, help_text="Whether players are split into teams.", verbose_name="teams enabled",
    )
    team_size = models.PositiveIntegerField(
        default=1, help_text="Players per team, when teams are enabled.", verbose_name="team size",
    )
    allow_spectators = models.BooleanField(
        default=True, help_text="Whether non-participants may spectate the battle.", verbose_name="allow spectators",
    )
    is_public = models.BooleanField(
        default=True,
        help_text="Public lobbies can be joined by anyone; private lobbies are invite-only.",
        verbose_name="public",
    )

    # -- Deck ---------------------------------------------------------------
    min_deck_size = models.PositiveIntegerField(
        default=1, help_text="Minimum number of balls a player must field.", verbose_name="minimum deck size",
    )
    max_deck_size = models.PositiveIntegerField(
        default=1, help_text="Maximum number of balls a player may field.", verbose_name="maximum deck size",
    )
    allow_duplicate_balls = models.BooleanField(
        default=True,
        help_text="Whether a player may field multiple balls of the same species.",
        verbose_name="allow duplicate balls",
    )
    allowed_rarities = models.JSONField(
        default=list, blank=True,
        help_text="Rarity values permitted in this mode. Empty means unrestricted.",
        verbose_name="allowed rarities",
    )
    blocked_rarities = models.JSONField(
        default=list, blank=True,
        help_text="Rarity values explicitly forbidden in this mode.", verbose_name="blocked rarities",
    )
    allowed_balls = models.ManyToManyField(
        "bd_models.Ball", blank=True, related_name="battle_modes_allowed",
        help_text="If set, only these ball species may be fielded in this mode.",
        verbose_name="allowed balls",
    )
    banned_balls = models.ManyToManyField(
        "bd_models.Ball", blank=True, related_name="battle_modes_banned",
        help_text="Ball species forbidden in this mode.", verbose_name="banned balls",
    )

    # -- Turn rules -----------------------------------------------------------
    turn_timer_seconds = models.PositiveIntegerField(
        default=15, help_text="Seconds each player has to choose an action on their turn.",
        verbose_name="turn timer (seconds)",
    )
    max_turns = models.PositiveIntegerField(
        default=30, help_text="Maximum number of turns before the battle is forced to a draw.",
        verbose_name="maximum turns",
    )
    battle_expiration_seconds = models.PositiveIntegerField(
        default=300, help_text="Seconds of total inactivity before an unfinished battle expires.",
        verbose_name="battle expiration (seconds)",
    )
    timeout_behavior = models.CharField(
        max_length=16, choices=TimeoutBehavior.choices, default=TimeoutBehavior.AUTO_DEFEND,
        help_text="What happens to a player who doesn't act before the turn timer runs out.",
        verbose_name="timeout behaviour",
    )

    # -- Battle rules -----------------------------------------------------------
    enabled_actions = models.JSONField(
        default=list, blank=True,
        help_text="Action keys enabled in this mode (attack, defend, counter, heal, dodge, ability). Empty means all enabled.",
        verbose_name="enabled actions",
    )

    # -- Rewards -----------------------------------------------------------
    win_multiplier = models.FloatField(
        default=1.0, help_text="Multiplier applied to the base win reward.", verbose_name="win multiplier",
    )
    loss_multiplier = models.FloatField(
        default=1.0, help_text="Multiplier applied to the base loss reward.", verbose_name="loss multiplier",
    )
    draw_multiplier = models.FloatField(
        default=1.0, help_text="Multiplier applied to the base draw reward.", verbose_name="draw multiplier",
    )
    streak_multiplier = models.FloatField(
        default=1.0, help_text="Multiplier applied to the base win-streak bonus.", verbose_name="streak multiplier",
    )

    # -- Misc -----------------------------------------------------------
    allow_surrender = models.BooleanField(
        default=True, help_text="Whether players may forfeit mid-battle.", verbose_name="allow surrender",
    )
    allow_rematch = models.BooleanField(
        default=True, help_text="Whether a rematch may be started from the result screen.",
        verbose_name="allow rematch",
    )

    created_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp this mode was created.", verbose_name="created at")
    updated_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp this mode was last modified.", verbose_name="updated at")

    class Meta:
        verbose_name = "battle mode"
        verbose_name_plural = "battle modes"
        ordering = ["-is_builtin", "name"]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if self.min_players > self.max_players:
            raise ValidationError("min_players cannot exceed max_players.")
        if self.min_deck_size > self.max_deck_size:
            raise ValidationError("min_deck_size cannot exceed max_deck_size.")
        if self.teams_enabled and self.team_size < 1:
            raise ValidationError("team_size must be at least 1 when teams are enabled.")

    def action_enabled(self, action_key: str) -> bool:
        if not self.enabled_actions:
            return True
        return action_key in self.enabled_actions


class Ability(models.Model):
    """A dashboard-authored ball ability.

    Behaviour is defined by Python source in `script`, run through
    `package/ability_sandbox.py`'s restricted interpreter — never
    `exec`'d against the real interpreter. The script may define any
    subset of the supported hook functions (see `ability_sandbox.HOOK_NAMES`):
    `execute` (fired when a player actively selects the Ability action) and
    passive hooks such as `battle_start`, `before_turn`, `after_turn`,
    `before_action`, `after_action`, `on_attack`, `on_defend`,
    `on_damage_taken`, `on_win`, `on_loss`.
    """

    class TriggerType(models.TextChoices):
        ACTIVE = "active", "Active (used via the Ability button)"
        PASSIVE = "passive", "Passive (triggers automatically on hooks)"

    objects: Manager[Self] = Manager()

    name = models.CharField(max_length=100, unique=True, help_text="Display name shown in battle embeds.", verbose_name="name")
    description = models.TextField(help_text="Player-facing description of what the ability does.", verbose_name="description")
    icon = models.CharField(max_length=100, blank=True, default="✨", help_text="Emoji or short icon token.", verbose_name="icon")
    trigger_type = models.CharField(
        max_length=8, choices=TriggerType.choices, default=TriggerType.ACTIVE,
        help_text="Whether this ability is manually activated or triggers automatically on battle hooks.",
        verbose_name="trigger type",
    )
    cooldown_turns = models.PositiveIntegerField(default=3, help_text="Turns before this ability can trigger again.", verbose_name="cooldown (turns)")
    uses_per_battle = models.PositiveIntegerField(default=1, help_text="Maximum triggers per battle.", verbose_name="uses per battle")

    allowed_balls = models.ManyToManyField(
        "bd_models.Ball", blank=True, related_name="battle_abilities",
        help_text="Ball species that may use this ability. Empty means unrestricted by species.", verbose_name="allowed balls",
    )
    allowed_rarities = models.JSONField(default=list, blank=True, help_text="Rarity values permitted. Empty means unrestricted.", verbose_name="allowed rarities")
    allowed_regimes = models.ManyToManyField(
        "bd_models.Regime", blank=True, related_name="battle_abilities",
        help_text="Regimes permitted to use this ability. Empty means unrestricted.", verbose_name="allowed regimes",
    )
    allowed_economies = models.ManyToManyField(
        "bd_models.Economy", blank=True, related_name="battle_abilities",
        help_text="Economies permitted to use this ability. Empty means unrestricted.", verbose_name="allowed economies",
    )
    allowed_modes = models.ManyToManyField(
        "battles.BattleMode", blank=True, related_name="allowed_abilities",
        help_text="Battle modes this ability may be used in. Empty means unrestricted.", verbose_name="allowed modes",
    )

    script = models.TextField(
        blank=True, default="",
        help_text="Python ability logic, run through the sandboxed Ability API. See the in-dashboard editor for available hooks and helpers.",
        verbose_name="ability script",
    )
    settings = models.JSONField(default=dict, blank=True, help_text="Free-form settings passed to the script as `ctx.settings`.", verbose_name="settings")

    is_enabled = models.BooleanField(default=True, help_text="Whether this ability is currently usable.", verbose_name="enabled")
    created_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp this ability was created.", verbose_name="created at")
    updated_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp this ability was last modified.", verbose_name="updated at")

    class Meta:
        verbose_name = "ability"
        verbose_name_plural = "abilities"

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if self.uses_per_battle < 1:
            raise ValidationError("uses_per_battle must be at least 1.")


class BattleReward(models.Model):
    """Base currency amounts for battle outcomes. Actual payouts are these
    values scaled by the active `BattleMode`'s win/loss/draw/streak
    multipliers.
    """

    objects: Manager[Self] = Manager()

    name = models.CharField(max_length=64, default="default", unique=True, help_text="Internal identifier for this reward profile.", verbose_name="reward profile name")
    is_active = models.BooleanField(default=True, help_text="Whether this reward profile is currently in use.", verbose_name="active")
    win_reward = models.PositiveIntegerField(default=50, help_text="Base currency awarded to the winner.", verbose_name="win reward")
    loss_reward = models.PositiveIntegerField(default=5, help_text="Base currency awarded to the loser.", verbose_name="loss reward")
    draw_reward = models.PositiveIntegerField(default=15, help_text="Base currency awarded to each player on a draw.", verbose_name="draw reward")
    win_streak_bonus = models.PositiveIntegerField(default=10, help_text="Base extra currency per consecutive win beyond the streak threshold.", verbose_name="win streak bonus")
    win_streak_threshold = models.PositiveIntegerField(default=3, help_text="Minimum consecutive wins required before streak bonuses apply.", verbose_name="win streak threshold")

    class Meta:
        verbose_name = "battle reward profile"
        verbose_name_plural = "battle reward profiles"

    def __str__(self) -> str:
        return f"BattleReward({self.name})"


class Battle(models.Model):
    """A single battle instance. Participants (2 for a Duel, up to
    `mode.max_players` for FFA/Team) live in `BattleParticipant`, not on
    this row, so the same model covers every mode type.
    """

    class Status(models.TextChoices):
        LOBBY = "lobby", "Lobby"
        ACTIVE = "active", "Active"
        FINISHED = "finished", "Finished"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    objects: Manager[Self] = Manager()

    mode = models.ForeignKey(
        "battles.BattleMode", on_delete=models.PROTECT, related_name="battles",
        help_text="The mode this battle is being played under.", verbose_name="mode",
    )
    guild_id = models.BigIntegerField(help_text="Discord guild ID the battle was started in.", verbose_name="guild ID")
    channel_id = models.BigIntegerField(help_text="Discord channel ID the battle message lives in.", verbose_name="channel ID")
    message_id = models.BigIntegerField(null=True, blank=True, help_text="Discord message ID of the live battle embed.", verbose_name="message ID")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.LOBBY, help_text="Current lifecycle state.", verbose_name="status")
    current_turn = models.PositiveIntegerField(default=0, help_text="Index of the current turn, starting at 0.", verbose_name="current turn")

    winner_participant = models.ForeignKey(
        "battles.BattleParticipant", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="won_battles", help_text="The winning participant, for Duel/FFA outcomes.", verbose_name="winner",
    )
    winner_team = models.IntegerField(null=True, blank=True, help_text="The winning team number, for Team Battle outcomes.", verbose_name="winner team")

    state = models.JSONField(
        default=dict, blank=True,
        help_text="Free-form engine state: active status effects, per-ability trigger counts, misc bookkeeping.",
        verbose_name="engine state",
    )
    config_snapshot = models.JSONField(
        default=dict, blank=True,
        help_text="Snapshot of BattleMode + BattleConfig values in effect when this battle started.",
        verbose_name="config snapshot",
    )

    created_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp the battle was created.", verbose_name="created at")
    last_action_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp of the most recent player action.", verbose_name="last action at")
    finished_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp the battle concluded.", verbose_name="finished at")

    class Meta:
        verbose_name = "battle"
        verbose_name_plural = "battles"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["guild_id", "channel_id"]),
        ]

    def __str__(self) -> str:
        return f"Battle #{self.pk} ({self.mode_id})"


class BattleParticipant(models.Model):
    """One player's seat in a battle: which ball they're fielding, their
    live HP/momentum/cooldowns, and (for Team Battle) which team they're on.
    """

    objects: Manager[Self] = Manager()

    battle = models.ForeignKey("battles.Battle", on_delete=models.CASCADE, related_name="participants", help_text="The battle this seat belongs to.", verbose_name="battle")
    user_id = models.BigIntegerField(help_text="Discord user ID of this participant.", verbose_name="user ID")
    ball_instance = models.ForeignKey(
        "bd_models.BallInstance", on_delete=models.CASCADE, related_name="battle_participations",
        help_text="The ball instance this participant is fielding.", verbose_name="ball instance",
    )
    team = models.IntegerField(null=True, blank=True, help_text="Team number, when the mode has teams enabled.", verbose_name="team")
    join_order = models.PositiveIntegerField(default=0, help_text="Order this participant joined the lobby, used for turn ordering.", verbose_name="join order")

    hp = models.IntegerField(default=0, help_text="Current HP.", verbose_name="HP")
    max_hp = models.IntegerField(default=0, help_text="Max HP at battle start, snapshotted from the ball's existing stats.", verbose_name="max HP")
    attack = models.IntegerField(default=0, help_text="Attack stat snapshotted at battle start.", verbose_name="attack")
    defense = models.IntegerField(default=0, help_text="Defense stat snapshotted at battle start.", verbose_name="defense")
    speed = models.IntegerField(default=0, help_text="Speed stat snapshotted at battle start.", verbose_name="speed")
    momentum = models.IntegerField(default=0, help_text="Current momentum value.", verbose_name="momentum")

    cooldowns = models.JSONField(default=dict, blank=True, help_text="Action/ability cooldowns keyed by action or ability id.", verbose_name="cooldowns")
    heal_uses = models.PositiveIntegerField(default=0, help_text="Heal uses consumed so far this battle.", verbose_name="heal uses")
    ability_uses = models.JSONField(default=dict, blank=True, help_text="Per-ability use counts this battle, keyed by ability id.", verbose_name="ability uses")

    is_alive = models.BooleanField(default=True, help_text="Whether this participant is still standing.", verbose_name="alive")
    is_spectator = models.BooleanField(default=False, help_text="Joined to watch rather than fight.", verbose_name="spectator")
    surrendered = models.BooleanField(default=False, help_text="Whether this participant forfeited.", verbose_name="surrendered")

    class Meta:
        verbose_name = "battle participant"
        verbose_name_plural = "battle participants"
        # A user may field more than one ball (up to the mode's
        # max_deck_size) via repeated /battle add calls — each is its own
        # combatant seat — so uniqueness is per (battle, user, ball), not
        # per (battle, user).
        unique_together = ("battle", "user_id", "ball_instance")
        ordering = ["battle", "join_order"]

    def __str__(self) -> str:
        return f"Participant {self.user_id} in Battle #{self.battle_id}"


class BattleTurn(models.Model):
    """A single resolved turn within a battle, kept for history/replay."""

    objects: Manager[Self] = Manager()

    battle = models.ForeignKey("battles.Battle", on_delete=models.CASCADE, related_name="turns", help_text="The battle this turn belongs to.", verbose_name="battle")
    turn_number = models.PositiveIntegerField(help_text="Index of this turn within the battle, starting at 0.", verbose_name="turn number")
    actions = models.JSONField(
        default=dict, blank=True,
        help_text='Chosen actions keyed by participant id, e.g. {"3": {"action": "attack", "target_id": 4}}.',
        verbose_name="actions",
    )
    result = models.JSONField(default=dict, blank=True, help_text="Structured resolution details keyed by participant id.", verbose_name="result")
    created_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp this turn was resolved.", verbose_name="created at")

    class Meta:
        verbose_name = "battle turn"
        verbose_name_plural = "battle turns"
        unique_together = ("battle", "turn_number")
        ordering = ["battle", "turn_number"]

    def __str__(self) -> str:
        return f"Turn {self.turn_number} of Battle #{self.battle_id}"
