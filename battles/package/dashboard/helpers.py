"""Small helpers shared by dashboard views."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils.decorators import method_decorator

staff_required = method_decorator(
    [login_required, user_passes_test(lambda u: u.is_staff)],
    name="dispatch",
)


def breadcrumbs(*labels: str) -> list[dict[str, str]]:
    return [{"label": label} for label in labels]
