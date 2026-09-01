"""audit logs

Revision ID: 029_audit_logs
Revises: 028_multi_carrier_negotiation
Create Date: 2026-09-01

Tabla genérica de auditoría (audit_logs): captura automática de cualquier
insert/update/delete que pase por el ORM de la app (ver el listener
before_flush en models.py). No requiere que los endpoints la llamen.
"""

from alembic import op
import sqlalchemy as sa

revision      = '029_audit_logs'
down_revision = '028_multi_carrier_negotiation'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE auditaction AS ENUM ('insert', 'update', 'delete');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            table_name          VARCHAR(100) NOT NULL,
            record_id           VARCHAR(100) NOT NULL,
            action              auditaction NOT NULL,
            changed_by_user_id  UUID REFERENCES users(id),
            company_id          UUID REFERENCES companies(id),
            changes             JSONB,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_audit_table_record ON audit_logs(table_name, record_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_audit_changed_by ON audit_logs(changed_by_user_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_audit_created_at ON audit_logs(created_at)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS audit_logs CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS auditaction"))
