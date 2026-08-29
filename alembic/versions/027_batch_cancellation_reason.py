"""add cancellation_reason + cancelled_at to route_batches

Revision ID: 027_batch_cancellation_reason
Revises: 026_message_templates
Create Date: 2026-08-21

Anular un lote ya aprobado es una acción más seria que cancelar un
borrador (ya se ofertó a transporte) — se exige un motivo, guardado acá
junto con la fecha en que se anuló. Cancelar un lote en draft sigue sin
requerir motivo (ver change_batch_status() en main.py).
"""

from alembic import op
import sqlalchemy as sa

revision      = '027_batch_cancellation_reason'
down_revision = '026_message_templates'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE route_batches
            ADD COLUMN IF NOT EXISTS cancellation_reason TEXT,
            ADD COLUMN IF NOT EXISTS cancelled_at         TIMESTAMPTZ
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE route_batches
            DROP COLUMN IF EXISTS cancellation_reason,
            DROP COLUMN IF EXISTS cancelled_at
    """))
