"""Optional lightweight dashboard views for the `battling` admin section.

Full CRUD for every model already exists for free via `battles/admin.py`
(registered with the standard Django admin, which is what the BallsDex
admin panel runs on). This sub-package adds a couple of purpose-built pages
on top of that — e.g. a single-page battle settings form — for hosts that
mount extra routes under something like `/battling/`.
"""
