"""add vehicle_type and service_type to route_batches

Revision ID: 020_batch_vehicle_service_type
Revises: 019_servicemode_paqueteria
Create Date: 2026-08-01

El tipo de vehículo y tipo de servicio se definen al crear el lote
y se guardan directamente en route_batches.
"""

from alembic import op
import sqlalchemy as sa

revision      = '020_batch_vehicle_service_type'
down_revision = '019_servicemode_paqueteria'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE route_batches
            ADD COLUMN IF NOT EXISTS vehicle_type VARCHAR(50),
            ADD COLUMN IF NOT EXISTS service_type VARCHAR(50)
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE route_batches
            DROP COLUMN IF EXISTS vehicle_type,
            DROP COLUMN IF EXISTS service_type
    """))
