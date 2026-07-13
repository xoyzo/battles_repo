"""Django admin registrations powering the `battling` dashboard section.

Every dashboard-editable knob described in the design (formula tuning,
modes/presets, abilities, rewards) lives here, and takes effect immediately
since the engine reads from the database each turn rather than caching
config at import time.
"""
from __future__ import annotations

from django.contrib import admin
from django.utils import timezone

from .models import (
    Ability,
    Battle,
    BattleConfig,
    BattleMode,
    BattleParticipant,
    BattleReward,
    BattleTurn,
)


@admin.register(BattleConfig)
class BattleConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "critical_hit_chance", "minimum_damage", "updated_at")
    list_filter = ("is_active",)
    fieldsets = (
        ("Profile", {"fields": ("name", "is_active")}),
        ("Damage formula", {"fields": (
            "attack_multiplier", "defense_multiplier", "minimum_damage",
            "critical_hit_chance", "critical_hit_multiplier",
        )}),
        ("Defend / Counter / Dodge / Heal", {"fields": (
            "defend_damage_reduction",
            "counter_cooldown_turns", "counter_reflect_multiplier",
            "dodge_cooldown_turns",
            "heal_uses_per_battle", "heal_amount_fraction",
        )}),
        ("Momentum", {"fields": (
            "momentum_min", "momentum_max",
            "momentum_high_threshold", "momentum_high_damage_bonus",
            "momentum_crit_threshold", "momentum_crit_bonus",
            "momentum_low_threshold", "momentum_low_damage_penalty",
            "afk_momentum_penalty",
        )}),
    )

    def save_model(self, request, obj, form, change):
        obj.updated_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(BattleMode)
class BattleModeAdmin(admin.ModelAdmin):
    list_display = ("name", "mode_type", "is_enabled", "is_builtin", "max_players", "teams_enabled", "turn_timer_seconds")
    list_filter = ("mode_type", "is_enabled", "is_builtin", "teams_enabled")
    search_fields = ("name", "description")
    filter_horizontal = ("allowed_balls", "banned_balls")
    fieldsets = (
        ("General", {"fields": ("name", "description", "icon", "is_enabled", "is_builtin", "mode_type")}),
        ("Players", {"fields": ("min_players", "max_players", "teams_enabled", "team_size", "allow_spectators", "is_public")}),
        ("Deck", {"fields": (
            "min_deck_size", "max_deck_size", "allow_duplicate_balls",
            "allowed_rarities", "blocked_rarities", "allowed_balls", "banned_balls",
        )}),
        ("Turn rules", {"fields": ("turn_timer_seconds", "max_turns", "battle_expiration_seconds", "timeout_behavior")}),
        ("Battle rules", {"fields": ("enabled_actions",)}),
        ("Rewards", {"fields": ("win_multiplier", "loss_multiplier", "draw_multiplier", "streak_multiplier")}),
        ("Misc", {"fields": ("allow_surrender", "allow_rematch")}),
    )

    def save_model(self, request, obj, form, change):
        now = timezone.now()
        if not change or obj.created_at is None:
            obj.created_at = now
        obj.updated_at = now
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_builtin:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Ability)
class AbilityAdmin(admin.ModelAdmin):
    list_display = ("name", "icon", "trigger_type", "cooldown_turns", "uses_per_battle", "is_enabled")
    list_filter = ("is_enabled", "trigger_type")
    search_fields = ("name", "description")
    filter_horizontal = ("allowed_balls", "allowed_regimes", "allowed_economies", "allowed_modes")
    fieldsets = (
        (None, {"fields": ("name", "description", "icon", "trigger_type", "is_enabled")}),
        ("Limits", {"fields": ("cooldown_turns", "uses_per_battle")}),
        ("Restrictions", {"fields": ("allowed_balls", "allowed_rarities", "allowed_regimes", "allowed_economies", "allowed_modes")}),
        ("Ability code", {
            "fields": ("script", "settings"),
            "description": (
                "Write Python here — it runs through a restricted sandbox (no imports, no file/network "
                "access, no dunder access), never the real interpreter. Define any of: execute(ctx) for the "
                "active Ability button, or passive hooks battle_start(ctx), before_turn(ctx), after_turn(ctx), "
                "before_action(ctx), after_action(ctx), on_attack(ctx), on_defend(ctx), on_damage_taken(ctx), "
                "on_win(ctx), on_loss(ctx). `ctx` exposes ctx.damage(), ctx.heal(), ctx.modify_stat(), "
                "ctx.add_effect(), ctx.change_momentum(), ctx.give_currency(), plus read-only helpers like "
                "ctx.get_teammates() / ctx.get_enemies() / ctx.self_economy / ctx.self_regime."
            ),
        }),
    )

    def save_model(self, request, obj, form, change):
        now = timezone.now()
        if not change or obj.created_at is None:
            obj.created_at = now
        obj.updated_at = now
        super().save_model(request, obj, form, change)


@admin.register(BattleReward)
class BattleRewardAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "win_reward", "loss_reward", "draw_reward", "win_streak_bonus")
    list_filter = ("is_active",)


class BattleParticipantInline(admin.TabularInline):
    model = BattleParticipant
    extra = 0
    readonly_fields = ("user_id", "ball_instance", "team", "hp", "max_hp", "momentum", "is_alive", "surrendered")
    can_delete = False


class BattleTurnInline(admin.TabularInline):
    model = BattleTurn
    extra = 0
    readonly_fields = ("turn_number", "actions", "result", "created_at")
    can_delete = False


@admin.register(Battle)
class BattleAdmin(admin.ModelAdmin):
    list_display = ("id", "mode", "status", "current_turn", "winner_participant", "winner_team", "created_at")
    list_filter = ("status", "mode")
    search_fields = ("guild_id", "channel_id")
    readonly_fields = (
        "mode", "guild_id", "channel_id", "message_id", "created_at",
        "last_action_at", "finished_at", "config_snapshot",
    )
    inlines = [BattleParticipantInline, BattleTurnInline]
