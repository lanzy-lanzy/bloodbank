# MySQL/MariaDB emulation of the conditional unique constraint
# ``one_active_allocation_per_bag`` (safety-critical: a blood bag may have at
# most one RESERVED/ISSUED allocation at a time).
#
# MySQL/MariaDB cannot create partial UNIQUE indexes, so Django skips that
# constraint on these backends (models.W036). We restore it with a STORED
# generated column that holds the bag_id only while the allocation is active,
# plus a UNIQUE index over it: inactive rows are NULL and never collide, two
# active rows for the same bag do.
#
# MySQL-only; SQLite/PostgreSQL keep the real partial index from 0001_initial.
from django.db import migrations

FORWARD = [
    "ALTER TABLE requests_allocation "
    "ADD COLUMN active_bag_key bigint "
    "GENERATED ALWAYS AS ("
    "CASE WHEN status IN ('RESERVED', 'ISSUED') THEN bag_id ELSE NULL END"
    ") STORED",
    "ALTER TABLE requests_allocation "
    "ADD CONSTRAINT uq_one_active_allocation_per_bag "
    "UNIQUE (active_bag_key)",
]

REVERSE = [
    "ALTER TABLE requests_allocation DROP INDEX uq_one_active_allocation_per_bag",
    "ALTER TABLE requests_allocation DROP COLUMN active_bag_key",
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
        ("requests", "0003_alter_bloodrequest_channel"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
