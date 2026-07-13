from django.apps import AppConfig


class BattlesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "battles"
    verbose_name = "Battles"

    # Tells BallsDex which dotted path holds the discord.py package
    # (the `setup(bot)` entry point) associated with this Django app.
    dpy_package = "battles.package"
