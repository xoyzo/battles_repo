"""Django models backing the battles package.

These models store everything the Discord-facing package needs: live battle
state, turn history, dynamically configurable abilities, per-ball battle
stats, and dashboard-editable configuration (timers, formulas, rewards).

Conventions followed (see repo-wide notes):
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
    """Singleton-style configuration for the battle engine.

    Only one row is expected to exist (enforced via `clean`), and every
    numeric/formula knob exposed to the dashboard lives here so the engine
    never hardcodes a value.
    """

    objects: Manager[Self] = Manager()

    name = models.CharField(
        max_length=64,
        default="default",
        unique=True,
        help_text="Internal identifier for this configuration profile.",
        verbose_name="configuration name",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this configuration profile is currently in use.",
        verbose_name="active",
    )

    turn_timer_seconds = models.PositiveIntegerField(
        default=15,
        help_text="Seconds each player has to choose an action on their turn.",
        verbose_name="turn timer (seconds)",
    )
    max_turns = models.PositiveIntegerField(
        default=30,
        help_text="Maximum number of turns before a battle is forced to a draw.",
        verbose_name="maximum turns",
    )
    battle_expiration_seconds = models.PositiveIntegerField(
        default=300,
        help_text="Seconds of total inactivity before an unfinished battle expires.",
        verbose_name="battle expiration (seconds)",
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
        default=5,
        help_text="Upper bound of the momentum meter.",
        verbose_name="momentum maximum",
    )
    momentum_min = models.IntegerField(
        default=-5,
        help_text="Lower bound of the momentum meter.",
        verbose_name="momentum minimum",
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

    enabled_actions = models.JSONField(
        default=list,
        blank=True,
        help_text="List of action keys enabled for battles (e.g. attack, defend, counter, heal, dodge, ability). Empty list means all actions are enabled.",
        verbose_name="enabled actions",
    )

    updated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp this configuration was last saved.",
        verbose_name="updated at",
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

    def action_enabled(self, action_key: str) -> bool:
        if not self.enabled_actions:
            return True
        return action_key in self.enabled_actions


class Ability(models.Model):
    """A dynamically defined, dashboard-authored ball ability.

    Abilities are not hardcoded in Python: the `effect` JSON field describes
    a declarative effect (or references a registered handler key) that the
    ability API (`package/ability_api.py`) interprets at battle time.
    """

    objects: Manager[Self] = Manager()

    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Display name of the ability shown in battle embeds.",
        verbose_name="name",
    )
    description = models.TextField(
        help_text="Player-facing description of what the ability does.",
        verbose_name="description",
    )
    icon = models.CharField(
        max_length=100,
        blank=True,
        default="✨",
        help_text="Emoji or short icon token shown next to the ability name.",
        verbose_name="icon",
    )
    cooldown_turns = models.PositiveIntegerField(
        default=3,
        help_text="Number of turns that must pass before this ability can be used again.",
        verbose_name="cooldown (turns)",
    )
    uses_per_battle = models.PositiveIntegerField(
        default=1,
        help_text="Maximum number of times this ability may be used in a single battle.",
        verbose_name="uses per battle",
    )

    allowed_balls = models.ManyToManyField(
        "bd_models.Ball",
        blank=True,
        related_name="battle_abilities",
        help_text="Ball species that may use this ability. Empty means unrestricted by species.",
        verbose_name="allowed balls",
    )
    allowed_rarities = models.JSONField(
        default=list,
        blank=True,
        help_text="List of rarity values permitted to use this ability. Empty means unrestricted.",
        verbose_name="allowed rarities",
    )
    allowed_regimes = models.ManyToManyField(
        "bd_models.Regime",
        blank=True,
        related_name="battle_abilities",
        help_text="Regimes permitted to use this ability. Empty means unrestricted.",
        verbose_name="allowed regimes",
    )

    effect = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Declarative effect definition consumed by the ability API, e.g. "
            '{"type": "buff_attack", "amount": 0.25, "duration": 3}.'
        ),
        verbose_name="effect definition",
    )
    script = models.TextField(
        blank=True,
        default="",
        help_text="Optional key of a registered custom Python handler for complex logic beyond the declarative effect system.",
        verbose_name="custom script key",
    )
    settings = models.JSONField(
        default=dict,
        blank=True,
        help_text="Free-form extra settings passed to the effect handler.",
        verbose_name="settings",
    )

    is_enabled = models.BooleanField(
        default=True,
        help_text="Whether this ability is currently usable in battles.",
        verbose_name="enabled",
    )
    created_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp this ability was created.",
        verbose_name="created at",
    )
    updated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp this ability was last modified.",
        verbose_name="updated at",
    )

    class Meta:
        verbose_name = "ability"
        verbose_name_plural = "abilities"

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if self.uses_per_battle < 1:
            raise ValidationError("uses_per_battle must be at least 1.")


class BallBattleStats(models.Model):
    """Battle stat block attached to a Ball species."""

    objects: Manager[Self] = Manager()

    ball = models.OneToOneField(
        "bd_models.Ball",
        on_delete=models.CASCADE,
        related_name="battle_stats",
        help_text="The ball species these battle stats belong to.",
        verbose_name="ball",
    )
    hp = models.PositiveIntegerField(
        default=100,
        help_text="Base maximum HP in battle.",
        verbose_name="HP",
    )
    attack = models.PositiveIntegerField(
        default=50,
        help_text="Base attack stat used in the damage formula.",
        verbose_name="attack",
    )
    defense = models.PositiveIntegerField(
        default=50,
        help_text="Base defense stat used in the damage formula.",
        verbose_name="defense",
    )
    speed = models.PositiveIntegerField(
        default=50,
        help_text="Base speed stat, used to break simultaneous-action ties.",
        verbose_name="speed",
    )
    battle_power = models.PositiveIntegerField(
        default=100,
        help_text="Aggregate battle power rating, shown in battle embeds and used for matchmaking balance.",
        verbose_name="battle power",
    )

    class Meta:
        verbose_name = "ball battle stats"
        verbose_name_plural = "ball battle stats"

    def __str__(self) -> str:
        return f"BattleStats({self.ball_id})"


class BattleReward(models.Model):
    """Configurable economy currency rewards for battle outcomes."""

    objects: Manager[Self] = Manager()

    name = models.CharField(
        max_length=64,
        default="default",
        unique=True,
        help_text="Internal identifier for this reward profile.",
        verbose_name="reward profile name",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this reward profile is currently in use.",
        verbose_name="active",
    )
    win_reward = models.PositiveIntegerField(
        default=50,
        help_text="Currency awarded to the winner of a battle.",
        verbose_name="win reward",
    )
    loss_reward = models.PositiveIntegerField(
        default=5,
        help_text="Currency awarded to the loser of a battle.",
        verbose_name="loss reward",
    )
    draw_reward = models.PositiveIntegerField(
        default=15,
        help_text="Currency awarded to each player when a battle ends in a draw.",
        verbose_name="draw reward",
    )
    win_streak_bonus = models.PositiveIntegerField(
        default=10,
        help_text="Extra currency awarded per consecutive win beyond the streak threshold.",
        verbose_name="win streak bonus",
    )
    win_streak_threshold = models.PositiveIntegerField(
        default=3,
        help_text="Minimum consecutive wins required before streak bonuses apply.",
        verbose_name="win streak threshold",
    )

    class Meta:
        verbose_name = "battle reward profile"
        verbose_name_plural = "battle reward profiles"

    def __str__(self) -> str:
        return f"BattleReward({self.name})"


class Battle(models.Model):
    """A single battle instance between two players."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        FINISHED = "finished", "Finished"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    objects: Manager[Self] = Manager()

    player_one_id = models.BigIntegerField(
        help_text="Discord user ID of player one (battle initiator).",
        verbose_name="player one ID",
    )
    player_two_id = models.BigIntegerField(
        help_text="Discord user ID of player two.",
        verbose_name="player two ID",
    )
    ball_one = models.ForeignKey(
        "bd_models.BallInstance",
        on_delete=models.CASCADE,
        related_name="battles_as_ball_one",
        help_text="Ball instance fielded by player one.",
        verbose_name="ball one",
    )
    ball_two = models.ForeignKey(
        "bd_models.BallInstance",
        on_delete=models.CASCADE,
        related_name="battles_as_ball_two",
        help_text="Ball instance fielded by player two.",
        verbose_name="ball two",
    )

    guild_id = models.BigIntegerField(
        help_text="Discord guild ID the battle was started in.",
        verbose_name="guild ID",
    )
    channel_id = models.BigIntegerField(
        help_text="Discord channel ID the battle message lives in.",
        verbose_name="channel ID",
    )
    message_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Discord message ID of the live battle embed.",
        verbose_name="message ID",
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Current lifecycle state of the battle.",
        verbose_name="status",
    )
    winner_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Discord user ID of the winner, if the battle has concluded and was not a draw.",
        verbose_name="winner ID",
    )
    current_turn = models.PositiveIntegerField(
        default=0,
        help_text="Index of the current turn, starting at 0.",
        verbose_name="current turn",
    )

    player_one_hp = models.IntegerField(
        default=0,
        help_text="Player one's ball's current HP.",
        verbose_name="player one HP",
    )
    player_two_hp = models.IntegerField(
        default=0,
        help_text="Player two's ball's current HP.",
        verbose_name="player two HP",
    )
    player_one_momentum = models.IntegerField(
        default=0,
        help_text="Player one's current momentum value.",
        verbose_name="player one momentum",
    )
    player_two_momentum = models.IntegerField(
        default=0,
        help_text="Player two's current momentum value.",
        verbose_name="player two momentum",
    )

    state = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Free-form engine state: action cooldowns, active status effects, "
            "heal usage counts, guard stacks, pending action selections."
        ),
        verbose_name="engine state",
    )
    config_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of the BattleConfig values in effect when this battle started, so live config edits don't retroactively change an in-progress battle.",
        verbose_name="config snapshot",
    )

    created_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp the battle was created.",
        verbose_name="created at",
    )
    last_action_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent player action, used for expiration checks.",
        verbose_name="last action at",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp the battle concluded.",
        verbose_name="finished at",
    )

    class Meta:
        verbose_name = "battle"
        verbose_name_plural = "battles"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["guild_id", "channel_id"]),
        ]

    def __str__(self) -> str:
        return f"Battle #{self.pk} ({self.player_one_id} vs {self.player_two_id})"

    def clean(self) -> None:
        if self.player_one_id == self.player_two_id:
            raise ValidationError("A player cannot battle themselves.")


class BattleTurn(models.Model):
    """A single resolved turn within a battle, kept for history/replay."""

    objects: Manager[Self] = Manager()

    battle = models.ForeignKey(
        "battles.Battle",
        on_delete=models.CASCADE,
        related_name="turns",
        help_text="The battle this turn belongs to.",
        verbose_name="battle",
    )
    turn_number = models.PositiveIntegerField(
        help_text="Index of this turn within the battle, starting at 0.",
        verbose_name="turn number",
    )
    player_one_action = models.CharField(
        max_length=32,
        help_text="Action key chosen by player one this turn (e.g. attack, defend).",
        verbose_name="player one action",
    )
    player_two_action = models.CharField(
        max_length=32,
        help_text="Action key chosen by player two this turn.",
        verbose_name="player two action",
    )
    result = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured resolution details: damage dealt, crits, effects applied, momentum changes.",
        verbose_name="result",
    )
    created_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp this turn was resolved.",
        verbose_name="created at",
    )

    class Meta:
        verbose_name = "battle turn"
        verbose_name_plural = "battle turns"
        unique_together = ("battle", "turn_number")
        ordering = ["battle", "turn_number"]

    def __str__(self) -> str:
        return f"Turn {self.turn_number} of Battle #{self.battle_id}"
