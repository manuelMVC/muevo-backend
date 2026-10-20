"""transport applications

Revision ID: 034_transport_applications
Revises: 033_profile_change_history
Create Date: 2026-10-20

Solicitudes de registro de empresas de transporte: un formulario público crea una
solicitud pendiente; un admin de Muevo la aprueba (crea la empresa verificada, su
administrador y su perfil) o la rechaza con un motivo.
"""

from alembic import op
import sqlalchemy as sa

revision      = '034_transport_applications'
down_revision = '033_profile_change_history'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS transport_applications (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            status                   VARCHAR(20)  NOT NULL DEFAULT 'pending',
            company_name             VARCHAR(255) NOT NULL,
            legal_name               VARCHAR(255) NOT NULL,
            tax_id                   VARCHAR(50)  NOT NULL,
            dot_number               VARCHAR(30),
            mc_number                VARCHAR(30),
            insurance_policy_number  VARCHAR(100) NOT NULL,
            insurance_expires        TIMESTAMPTZ  NOT NULL,
            service_modes            VARCHAR[]    NOT NULL DEFAULT '{}',
            fleet_size               INTEGER,
            contact_name             VARCHAR(255) NOT NULL,
            contact_email            VARCHAR(255) NOT NULL,
            contact_phone            VARCHAR(30)  NOT NULL,
            address_line1            VARCHAR(255),
            city                     VARCHAR(100) NOT NULL,
            state                    VARCHAR(50)  NOT NULL,
            zip_code                 VARCHAR(20),
            admin_full_name          VARCHAR(255) NOT NULL,
            admin_email              VARCHAR(255) NOT NULL,
            admin_password_hash      VARCHAR(255) NOT NULL,
            reviewed_by_id           UUID REFERENCES users(id),
            reviewed_at              TIMESTAMPTZ,
            rejection_reason         TEXT,
            transport_company_id     UUID REFERENCES transport_companies(id),
            created_at               TIMESTAMPTZ DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_transport_applications_status ON transport_applications(status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_transport_applications_admin_email ON transport_applications(admin_email)"))


def downgrade() -> None:
    op.get_bind().execute(sa.text("DROP TABLE IF EXISTS transport_applications CASCADE"))
