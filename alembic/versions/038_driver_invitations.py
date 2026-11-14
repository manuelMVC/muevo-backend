"""driver invitations

Revision ID: 038_driver_invitations
Revises: 037_batch_number_per_company
Create Date: 2026-11-14

Invitaciones de una empresa de transporte a sus conductores. Se guarda solo el hash del token;
el enlace con el token en claro va únicamente en el correo y en la respuesta de creación.
"""

from alembic import op
import sqlalchemy as sa

revision      = '038_driver_invitations'
down_revision = '037_batch_number_per_company'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS driver_invitations (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            transport_company_id  UUID NOT NULL REFERENCES transport_companies(id) ON DELETE CASCADE,
            invited_by_id         UUID REFERENCES users(id),
            email                 VARCHAR(255) NOT NULL,
            full_name             VARCHAR(255),
            token_hash            VARCHAR(64)  NOT NULL UNIQUE,
            status                VARCHAR(20)  NOT NULL DEFAULT 'pending',
            expires_at            TIMESTAMPTZ  NOT NULL,
            accepted_at           TIMESTAMPTZ,
            driver_id             UUID REFERENCES drivers(id) ON DELETE SET NULL,
            created_at            TIMESTAMPTZ DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_driver_invitations_company_status ON driver_invitations(transport_company_id, status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_driver_invitations_email ON driver_invitations(email)"))


def downgrade() -> None:
    op.get_bind().execute(sa.text("DROP TABLE IF EXISTS driver_invitations CASCADE"))
