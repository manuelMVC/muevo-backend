"""add company_admins table

Revision ID: 004_company_admins
Revises: 003_deliveries_route_detail
Create Date: 2026-06-22

Permite vincular usuarios como administradores de una empresa cliente
(company) — necesario para el portal warehouse.
"""

from alembic import op
import sqlalchemy as sa

revision = '004_company_admins'
down_revision = '003_deliveries_route_detail'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS company_admins (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id   UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            user_id      UUID NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
            is_primary   BOOLEAN NOT NULL DEFAULT FALSE,
            can_dispatch BOOLEAN NOT NULL DEFAULT TRUE,
            can_invoice  BOOLEAN NOT NULL DEFAULT FALSE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(company_id, user_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_company_admins_company_id ON company_admins(company_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_company_admins_user_id    ON company_admins(user_id)"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS company_admins CASCADE"))
