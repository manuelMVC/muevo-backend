"""add holding_user_companies + extend holding_users

Revision ID: 008_holding_user_companies
Revises: 007_batches_and_incidents
Create Date: 2026-07-01

Cambios:
- holding_users: agrega is_super_admin, is_active, updated_at
- Nueva tabla holding_user_companies: acceso granular por compañía
- Migra company_admins existentes al nuevo schema:
    carlos@muevo.app → holding_user de LegalDocs Express LLC
    con acceso a la compañía LegalDocs Express LLC (can_view, can_operate, can_invoice, is_admin)
"""

from alembic import op
import sqlalchemy as sa

revision      = '008_holding_user_companies'
down_revision = '007_batches_and_incidents'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Extender holding_users ─────────────────────────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE holding_users
            ADD COLUMN IF NOT EXISTS is_super_admin BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS is_active      BOOLEAN NOT NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    """))

    # ── 2. Crear holding_user_companies ───────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS holding_user_companies (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            holding_user_id UUID NOT NULL REFERENCES holding_users(id) ON DELETE CASCADE,
            company_id      UUID NOT NULL REFERENCES companies(id)      ON DELETE CASCADE,
            can_view        BOOLEAN NOT NULL DEFAULT TRUE,
            can_operate     BOOLEAN NOT NULL DEFAULT FALSE,
            can_invoice     BOOLEAN NOT NULL DEFAULT FALSE,
            is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
            granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            granted_by_id   UUID REFERENCES users(id),
            UNIQUE(holding_user_id, company_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_huc_holding_user_id ON holding_user_companies(holding_user_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_huc_company_id      ON holding_user_companies(company_id)"))

    # ── 3. Migrar company_admins existentes al nuevo schema ───────────────────
    # Para cada company_admin, buscamos si el user ya tiene un holding_user
    # en el holding de esa compañía. Si no, lo creamos como super_admin.
    # Luego creamos el HoldingUserCompany con todos los permisos.
    conn.execute(sa.text("""
        INSERT INTO holding_user_companies (
            id, holding_user_id, company_id,
            can_view, can_operate, can_invoice, is_admin,
            granted_at
        )
        SELECT
            gen_random_uuid(),
            hu.id,
            ca.company_id,
            TRUE, TRUE, TRUE, TRUE,
            NOW()
        FROM company_admins ca
        JOIN companies c ON c.id = ca.company_id
        JOIN holding_users hu ON hu.holding_id = c.holding_id AND hu.user_id = ca.user_id
        ON CONFLICT (holding_user_id, company_id) DO NOTHING
    """))

    # Para company_admins cuyo user NO tiene holding_user todavía, crearlo como super_admin
    conn.execute(sa.text("""
        INSERT INTO holding_users (
            id, holding_id, user_id, role, is_super_admin, is_active,
            can_create_companies, can_view_consolidated_billing
        )
        SELECT
            gen_random_uuid(),
            c.holding_id,
            ca.user_id,
            'super_admin',
            TRUE,
            TRUE,
            TRUE,
            TRUE
        FROM company_admins ca
        JOIN companies c ON c.id = ca.company_id
        WHERE NOT EXISTS (
            SELECT 1 FROM holding_users hu
            WHERE hu.holding_id = c.holding_id AND hu.user_id = ca.user_id
        )
        ON CONFLICT (holding_id, user_id) DO NOTHING
    """))

    # Ahora crear los HoldingUserCompany para los recién insertados
    conn.execute(sa.text("""
        INSERT INTO holding_user_companies (
            id, holding_user_id, company_id,
            can_view, can_operate, can_invoice, is_admin
        )
        SELECT
            gen_random_uuid(),
            hu.id,
            ca.company_id,
            TRUE, TRUE, TRUE, TRUE
        FROM company_admins ca
        JOIN companies c  ON c.id  = ca.company_id
        JOIN holding_users hu ON hu.holding_id = c.holding_id AND hu.user_id = ca.user_id
        ON CONFLICT (holding_user_id, company_id) DO NOTHING
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS holding_user_companies CASCADE"))
    conn.execute(sa.text("ALTER TABLE holding_users DROP COLUMN IF EXISTS is_super_admin"))
    conn.execute(sa.text("ALTER TABLE holding_users DROP COLUMN IF EXISTS is_active"))
    conn.execute(sa.text("ALTER TABLE holding_users DROP COLUMN IF EXISTS updated_at"))
