"""add transport_company_admins table

Revision ID: 006_transport_company_admins
Revises: 005_contract_status
Create Date: 2026-06-28

Permite vincular usuarios como administradores de una empresa de
transporte — necesario para el portal de la empresa de transporte
(transport.html): aceptar/rechazar rutas, gestionar flota, y ver
facturación consolidada.
"""

from alembic import op
import sqlalchemy as sa

revision = '006_transport_company_admins'
down_revision = '005_contract_status'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS transport_company_admins (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            transport_company_id  UUID NOT NULL REFERENCES transport_companies(id) ON DELETE CASCADE,
            user_id                UUID NOT NULL REFERENCES users(id)               ON DELETE CASCADE,
            is_primary             BOOLEAN NOT NULL DEFAULT FALSE,
            can_accept_routes      BOOLEAN NOT NULL DEFAULT TRUE,
            can_manage_fleet       BOOLEAN NOT NULL DEFAULT TRUE,
            can_view_billing       BOOLEAN NOT NULL DEFAULT FALSE,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(transport_company_id, user_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_tca_transport_company_id ON transport_company_admins(transport_company_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_tca_user_id              ON transport_company_admins(user_id)"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS transport_company_admins CASCADE"))
