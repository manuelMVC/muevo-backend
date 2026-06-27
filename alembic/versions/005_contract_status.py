"""add status flow to contracts

Revision ID: 005_contract_status
Revises: 004_company_admins
Create Date: 2026-06-27

Agrega el campo `status` a contracts con el flujo:
draft -> pending_signature -> active -> (suspended | expired | renewed | cancelled)

`is_active` se conserva por retrocompatibilidad y se mantiene sincronizado
automáticamente por un trigger: True únicamente cuando status='active'.
"""

from alembic import op
import sqlalchemy as sa

revision = '005_contract_status'
down_revision = '004_company_admins'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Agregar la columna status con default 'draft'
    conn.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='contracts' AND column_name='status') THEN
                ALTER TABLE contracts ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'draft';
            END IF;
        END $$;
    """))

    # 2. Backfill: los contratos existentes con is_active=TRUE pasan a 'active',
    #    los is_active=FALSE pasan a 'cancelled' (estado terminal más conservador
    #    — no asumimos que estaban vencidos o renovados sin esa información).
    conn.execute(sa.text("""
        UPDATE contracts SET status = 'active'    WHERE is_active = TRUE  AND status = 'draft'
    """))
    conn.execute(sa.text("""
        UPDATE contracts SET status = 'cancelled' WHERE is_active = FALSE AND status = 'draft'
    """))

    # 3. Constraint de valores válidos para status
    conn.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_contract_status_valid'
            ) THEN
                ALTER TABLE contracts ADD CONSTRAINT ck_contract_status_valid
                    CHECK (status IN ('draft','pending_signature','active','suspended','expired','renewed','cancelled'));
            END IF;
        END $$;
    """))

    # 4. Columna renewed_into_id — apunta al contrato sucesor si status='renewed'
    conn.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='contracts' AND column_name='renewed_into_id') THEN
                ALTER TABLE contracts ADD COLUMN renewed_into_id UUID REFERENCES contracts(id);
            END IF;
        END $$;
    """))

    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_contracts_status ON contracts(status)"))

    # 5. Trigger: mantiene is_active sincronizado con status automáticamente
    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION fn_sync_contract_is_active()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.is_active := (NEW.status = 'active');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_contract_sync_is_active ON contracts"))
    conn.execute(sa.text("""
        CREATE TRIGGER trg_contract_sync_is_active
        BEFORE INSERT OR UPDATE OF status ON contracts
        FOR EACH ROW EXECUTE FUNCTION fn_sync_contract_is_active()
    """))

    # 6. Aplicar el trigger una vez a los datos existentes para que
    #    is_active quede perfectamente sincronizado tras el backfill
    conn.execute(sa.text("UPDATE contracts SET status = status"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_contract_sync_is_active ON contracts"))
    conn.execute(sa.text("DROP FUNCTION IF EXISTS fn_sync_contract_is_active"))
    conn.execute(sa.text("ALTER TABLE contracts DROP CONSTRAINT IF EXISTS ck_contract_status_valid"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_contracts_status"))
    conn.execute(sa.text("ALTER TABLE contracts DROP COLUMN IF EXISTS renewed_into_id"))
    conn.execute(sa.text("ALTER TABLE contracts DROP COLUMN IF EXISTS status"))
