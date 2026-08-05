"""remove 'offered' from batchstatus enum

Revision ID: 010_remove_offered_batchstatus
Revises: 009_batch_status_approved
Create Date: 2026-07-01

Elimina el valor 'offered' del enum batchstatus en PostgreSQL.
PostgreSQL no soporta DROP VALUE directamente — se recrea el tipo.

Nuevo flujo: draft → approved → partially_accepted | fully_accepted
             → partially_completed → completed | closed_with_incidents
             → cancelled | expired
"""

from alembic import op
import sqlalchemy as sa

revision      = '010_remove_offered_batchstatus'
down_revision = '009_batch_status_approved'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # PostgreSQL no permite DROP VALUE en enums directamente.
    # Estrategia: renombrar el tipo viejo, crear el nuevo, migrar los datos,
    # eliminar el tipo viejo.

    conn.execute(sa.text("ALTER TYPE batchstatus RENAME TO batchstatus_old"))

    conn.execute(sa.text("""
        CREATE TYPE batchstatus AS ENUM (
            'draft', 'approved', 'partially_accepted', 'fully_accepted',
            'partially_completed', 'completed', 'closed_with_incidents',
            'cancelled', 'expired'
        )
    """))

    # Migrar columna status en route_batches
    conn.execute(sa.text("""
        ALTER TABLE route_batches
            ALTER COLUMN status DROP DEFAULT
    """))
    conn.execute(sa.text("""
        ALTER TABLE route_batches
            ALTER COLUMN status TYPE batchstatus
            USING CASE status::text
                WHEN 'offered' THEN 'approved'::batchstatus
                ELSE status::text::batchstatus
            END
    """))
    conn.execute(sa.text("""
        ALTER TABLE route_batches
            ALTER COLUMN status SET DEFAULT 'draft'
    """))

    conn.execute(sa.text("DROP TYPE batchstatus_old"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TYPE batchstatus RENAME TO batchstatus_old"))
    conn.execute(sa.text("""
        CREATE TYPE batchstatus AS ENUM (
            'draft', 'approved', 'offered', 'partially_accepted', 'fully_accepted',
            'partially_completed', 'completed', 'closed_with_incidents',
            'cancelled', 'expired'
        )
    """))
    conn.execute(sa.text("""
        ALTER TABLE route_batches
            ALTER COLUMN status TYPE batchstatus
            USING status::text::batchstatus
    """))
    conn.execute(sa.text("DROP TYPE batchstatus_old"))
