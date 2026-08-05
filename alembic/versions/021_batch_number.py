"""add batch_number sequential identifier to route_batches

Revision ID: 021_batch_number
Revises: 020_batch_vehicle_service_type
Create Date: 2026-08-03

Agrega un identificador numérico secuencial y legible para los lotes,
formato ORL-LOT-0001. Independiente del route_number de cada ruta.
"""

from alembic import op
import sqlalchemy as sa

revision      = '021_batch_number'
down_revision = '020_batch_vehicle_service_type'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE route_batches
            ADD COLUMN IF NOT EXISTS batch_number VARCHAR(50) UNIQUE
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_route_batches_batch_number ON route_batches(batch_number)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE route_batches DROP COLUMN IF EXISTS batch_number"))
