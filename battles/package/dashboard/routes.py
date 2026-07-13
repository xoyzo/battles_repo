"""URL routes for the `/battling` dashboard section.

Mount these into the host admin site's urlconf, e.g.:

    from battles.package.dashboard.routes import urlpatterns as battling_urls
    urlpatterns += [path("battling/", include(battling_urls))]

Templates aren't bundled (the host project may already have an admin
layout to extend) — point `template_name` at whatever base template your
dashboard uses, or fall back to Django admin, which already has full CRUD
for every model via `battles/admin.py`. The Ability editor in particular
is where you'd wire in a real code-editor widget (CodeMirror/Ace/Monaco)
around the `script` textarea for syntax highlighting.
"""
from __future__ import annotations

from django.urls import path
from django.views.generic import ListView, UpdateView
from django.views.generic.edit import FormView

from battles.models import Ability, BattleConfig, BattleMode, BattleReward

from .forms import AbilityForm, BattleConfigForm, BattleModeForm, BattleRewardForm
from .helpers import staff_required


@staff_required
class BattleSettingsView(FormView):
    template_name = "battling/settings.html"
    form_class = BattleConfigForm
    success_url = "/battling/"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        config, _ = BattleConfig.objects.get_or_create(name="default", defaults={"is_active": True})
        kwargs["instance"] = config
        return kwargs

    def form_valid(self, form):
        from django.utils import timezone

        instance = form.save(commit=False)
        instance.updated_at = timezone.now()
        instance.save()
        return super().form_valid(form)


@staff_required
class ModeListView(ListView):
    template_name = "battling/modes.html"
    model = BattleMode
    context_object_name = "modes"
    paginate_by = 25


@staff_required
class ModeEditView(UpdateView):
    template_name = "battling/mode_edit.html"
    model = BattleMode
    form_class = BattleModeForm
    success_url = "/battling/modes/"

    def form_valid(self, form):
        from django.utils import timezone

        instance = form.save(commit=False)
        now = timezone.now()
        if instance.created_at is None:
            instance.created_at = now
        instance.updated_at = now
        instance.save()
        form.save_m2m()
        return super().form_valid(form)


@staff_required
class AbilityListView(ListView):
    template_name = "battling/abilities.html"
    model = Ability
    context_object_name = "abilities"
    paginate_by = 25


@staff_required
class AbilityEditView(UpdateView):
    template_name = "battling/ability_edit.html"
    model = Ability
    form_class = AbilityForm
    success_url = "/battling/abilities/"

    def form_valid(self, form):
        from django.utils import timezone

        from ..ability_sandbox import validate_script

        errors = validate_script(form.cleaned_data.get("script", ""))
        if errors:
            form.add_error("script", "; ".join(errors))
            return self.form_invalid(form)

        instance = form.save(commit=False)
        now = timezone.now()
        if instance.created_at is None:
            instance.created_at = now
        instance.updated_at = now
        instance.save()
        form.save_m2m()
        return super().form_valid(form)


@staff_required
class RewardsView(FormView):
    template_name = "battling/rewards.html"
    form_class = BattleRewardForm
    success_url = "/battling/rewards/"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        reward, _ = BattleReward.objects.get_or_create(name="default", defaults={"is_active": True})
        kwargs["instance"] = reward
        return kwargs


urlpatterns = [
    path("", BattleSettingsView.as_view(), name="battling-settings"),
    path("modes/", ModeListView.as_view(), name="battling-modes"),
    path("modes/<int:pk>/", ModeEditView.as_view(), name="battling-mode-edit"),
    path("abilities/", AbilityListView.as_view(), name="battling-abilities"),
    path("abilities/<int:pk>/", AbilityEditView.as_view(), name="battling-ability-edit"),
    path("rewards/", RewardsView.as_view(), name="battling-rewards"),
]
