"""add paqueteria to servicemode enum

Revision ID: 019_servicemode_paqueteria
Revises: 018_batch_clients_fields
Create Date: 2026-08-01

Agrega el valor 'paqueteria' al enum servicemode de PostgreSQL.
"""

from alembic import op
import sqlalchemy as sa

revision      = '019_servicemode_paqueteria'
down_revision = '018_batch_clients_fields'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TYPE servicemode ADD VALUE IF NOT EXISTS 'paqueteria'"
    ))


def downgrade() -> None:
    # PostgreSQL no soporta eliminar valores de un enum directamente.
    # Para downgrade habría que recrear el enum sin 'paqueteria'.
    pass
