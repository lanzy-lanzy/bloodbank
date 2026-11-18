from django.db import migrations


TEMPLATES = (
    ("appointment_confirmed_sms", "Donation appointment confirmed",
     "Blood Bank: Your donation appointment is confirmed for {{ appointment_date }} "
     "at {{ appointment_time }}{{ location }}."),
    ("request_approved_sms", "Blood request approved (SMS)",
     "Blood Bank: Request {{ request_id }} was approved. {{ stock_message }}"),
    ("request_rejected_sms", "Blood request rejected (SMS)",
     "Blood Bank: Request {{ request_id }} was rejected. Sign in for details."),
)


def add_templates(apps, schema_editor):
    templates = apps.get_model("notifications", "NotificationTemplate")
    for code, subject, body in TEMPLATES:
        templates.objects.using(schema_editor.connection.alias).get_or_create(
            code=code, defaults={"name": subject, "subject": subject, "body": body, "channels": "sms"},
        )


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial")]
    operations = [migrations.RunPython(add_templates, migrations.RunPython.noop)]
