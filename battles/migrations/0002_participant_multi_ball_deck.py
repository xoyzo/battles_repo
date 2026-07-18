from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("battles", "0001_initial"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="battleparticipant",
            unique_together={("battle", "user_id", "ball_instance")},
        ),
    ]
