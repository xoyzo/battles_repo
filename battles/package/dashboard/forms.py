"""Django ModelForms backing the `/battling` dashboard pages."""
from __future__ import annotations

from django import forms

from battles.models import Ability, BattleConfig, BattleMode, BattleReward


class BattleConfigForm(forms.ModelForm):
    class Meta:
        model = BattleConfig
        exclude = ["updated_at"]


_ACTION_CHOICES = [
    ("attack", "Attack"), ("defend", "Defend"), ("counter", "Counter"),
    ("heal", "Heal"), ("dodge", "Dodge"), ("ability", "Ability"),
]


class BattleModeForm(forms.ModelForm):
    # `enabled_actions` is a JSONField, whose auto-generated form field
    # expects a raw JSON *string* to parse. Pairing that with a
    # CheckboxSelectMultiple widget (which submits a list) crashes on
    # save — `forms.JSONField.to_python()` can't `json.loads()` a list.
    # Declaring it explicitly as a MultipleChoiceField sidesteps the
    # auto-generated field entirely; Django assigns the resulting list
    # straight onto the JSONField model attribute, which is exactly what
    # a JSONField expects.
    enabled_actions = forms.MultipleChoiceField(
        choices=_ACTION_CHOICES, required=False, widget=forms.CheckboxSelectMultiple,
        help_text="Leave everything unchecked to enable all actions.",
    )

    class Meta:
        model = BattleMode
        exclude = ["created_at", "updated_at"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
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
