"""add 'approved' to batchstatus enum

Revision ID: 009_batch_status_approved
Revises: 008_holding_user_companies
Create Date: 2026-07-01

Agrega el estado 'approved' al enum batchstatus de PostgreSQL.
El estado va entre 'draft' y 'offered' en el flujo de vida de un lote.

Flujo actualizado:
  draft → approved → offered → partially_accepted | fully_accepted
       → partially_completed → completed | closed_with_incidents
       → cancelled | expired
"""

from alembic import op
import sqlalchemy as sa

revision      = '009_batch_status_approved'
down_revision = '008_holding_user_companies'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    # PostgreSQL requiere ADD VALUE para extender un enum existente
    conn.execute(sa.text(
        "ALTER TYPE batchstatus ADD VALUE IF NOT EXISTS 'approved' AFTER 'draft'"
    ))


def downgrade() -> None:
    # PostgreSQL no soporta DROP VALUE de un enum directamente.
    # Para hacer downgrade habría que recrear el tipo completo sin 'approved'.
    # Dejamos el downgrade como no-op ya que es una adición no destructiva.
    pass
