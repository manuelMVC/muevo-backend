"""notifications

Revision ID: 030_notifications
Revises: 029_audit_logs
Create Date: 2026-09-11

Tabla de notificaciones in-app para usuarios del warehouse y del portal
transportista (distinta de notify_batch_client, que es para el contacto
externo del cliente). type es texto libre, no un enum de Postgres, para
no requerir una migración nueva cada vez que se agregue un tipo.
"""

from alembic import op
import sqlalchemy as sa

revision      = '030_notifications'
down_revision = '029_audit_logs'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS notifications (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            type                   VARCHAR(60) NOT NULL,
            title                  VARCHAR(200) NOT NULL,
            body                   TEXT,
            entity_type            VARCHAR(50),
            entity_id              VARCHAR(100),
            company_id             UUID REFERENCES companies(id),
            transport_company_id   UUID REFERENCES transport_companies(id),
            is_read                BOOLEAN NOT NULL DEFAULT FALSE,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            read_at                TIMESTAMPTZ
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_notifications_user_unread ON notifications(user_id, is_read)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_notifications_user_created ON notifications(user_id, created_at)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS notifications CASCADE"))
