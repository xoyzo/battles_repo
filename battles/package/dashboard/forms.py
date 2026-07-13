"""Django ModelForms backing the `/battling` dashboard pages."""
from __future__ import annotations

from django import forms

from battles.models import Ability, BattleConfig, BattleMode, BattleReward


class BattleConfigForm(forms.ModelForm):
    class Meta:
        model = BattleConfig
        exclude = ["updated_at"]


class BattleModeForm(forms.ModelForm):
    class Meta:
        model = BattleMode
        exclude = ["created_at", "updated_at"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "enabled_actions": forms.CheckboxSelectMultiple(choices=[
                ("attack", "Attack"), ("defend", "Defend"), ("counter", "Counter"),
                ("heal", "Heal"), ("dodge", "Dodge"), ("ability", "Ability"),
            ]),
            "allowed_rarities": forms.TextInput(attrs={"placeholder": '["common", "rare"] (JSON list, empty = unrestricted)'}),
            "blocked_rarities": forms.TextInput(attrs={"placeholder": '["legendary"] (JSON list)'}),
        }


class AbilityForm(forms.ModelForm):
    class Meta:
        model = Ability
        exclude = ["created_at", "updated_at"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            # Swap for a real code-editor widget (CodeMirror/Ace/Monaco) at
            # the template layer if your dashboard has one available; a
            # plain textarea is the safe, dependency-free default.
            "script": forms.Textarea(attrs={"rows": 16, "class": "code-editor", "spellcheck": "false", "placeholder": "def execute(ctx):\n    ctx.damage(15, target=\"opponent\")\n"}),
            "settings": forms.Textarea(attrs={"rows": 3}),
        }


class BattleRewardForm(forms.ModelForm):
    class Meta:
        model = BattleReward
        fields = "__all__"
