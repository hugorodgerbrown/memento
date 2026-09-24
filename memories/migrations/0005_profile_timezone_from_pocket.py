"""Copy each owner's time zone from PocketLink to Profile. Data only; the field goes in 0006."""

from django.db import migrations


def forwards(apps, schema_editor):
    PocketLink = apps.get_model("memories", "PocketLink")
    Profile = apps.get_model("memories", "Profile")
    for link in PocketLink.objects.all():
        Profile.objects.update_or_create(
            owner_id=link.owner_id, defaults={"timezone": link.timezone}
        )


def backwards(apps, schema_editor):
    PocketLink = apps.get_model("memories", "PocketLink")
    Profile = apps.get_model("memories", "Profile")
    for profile in Profile.objects.all():
        PocketLink.objects.filter(owner_id=profile.owner_id).update(timezone=profile.timezone)


class Migration(migrations.Migration):
    dependencies = [("memories", "0004_profile")]

    operations = [migrations.RunPython(forwards, backwards)]
