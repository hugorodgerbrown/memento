from django.db import migrations, models


class Migration(migrations.Migration):
    """0022, widened: any single speaker, not only Pocket's placeholder. The tick is kept."""

    dependencies = [
        ("memories", "0007_pocketlink_one_unnamed_speaker_is_me"),
    ]

    operations = [
        migrations.RenameField(
            model_name="pocketlink",
            old_name="one_unnamed_speaker_is_me",
            new_name="one_speaker_is_me",
        ),
        migrations.AlterField(
            model_name="pocketlink",
            name="one_speaker_is_me",
            field=models.BooleanField(
                default=False,
                help_text="Keep every recording with exactly one speaker, whatever Pocket "
                "calls them. Two or more speakers, or no speaker labels, are still skipped.",
            ),
        ),
    ]
