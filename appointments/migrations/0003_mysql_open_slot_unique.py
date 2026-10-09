# MySQL/MariaDB emulation of the conditional unique constraint
# ``unique_open_appointment_slot`` declared in 0002_initial.
#
# MySQL and MariaDB cannot create *partial* (conditional) UNIQUE indexes, so
# Django silently skips that constraint on these backends (models.W036) and the
# "one open appointment per donor/date/time" race guard would be lost.
#
# We restore the guarantee with the standard workaround: a STORED generated
# column that is non-NULL only while the appointment is open, plus a plain
# UNIQUE index over it. NULLs are never equal in a MySQL UNIQUE index, so
# closed rows (NULL) never collide, while two open rows in the same slot do.
#
# This is MySQL-only: SQLite/PostgreSQL already enforce the real partial index
# from 0002_initial, so the function is a no-op there.
from django.db import migrations

FORWARD = [
    "ALTER TABLE appointments_appointment "
    "ADD COLUMN open_slot_key date "
    "GENERATED ALWAYS AS ("
    "CASE WHEN status IN ('CANCELLED', 'NO_SHOW', 'COMPLETED') "
    "THEN NULL ELSE `date` END"
    ") STORED",
    "ALTER TABLE appointments_appointment "
    "ADD CONSTRAINT uq_open_appointment_slot "
    "UNIQUE (donor_id, open_slot_key, `time`)",
]

REVERSE = [
    "ALTER TABLE appointments_appointment DROP INDEX uq_open_appointment_slot",
    "ALTER TABLE appointments_appointment DROP COLUMN open_slot_key",
]


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    for sql in FORWARD:
        schema_editor.execute(sql)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    for sql in REVERSE:
        schema_editor.execute(sql)


class Migration(migrations.Migration):

    # MySQL cannot run DDL inside a transaction, so this migration is
    # intentionally non-atomic (SQLite/PostgreSQL are unaffected: it's a no-op).
    atomic = False

    dependencies = [
        ("appointments", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
