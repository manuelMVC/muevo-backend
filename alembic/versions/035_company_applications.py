"""company applications

Revision ID: 035_company_applications
Revises: 034_transport_applications
Create Date: 2026-10-21

Solicitudes de registro de warehouses (compañías cliente). Un formulario público crea
una solicitud pendiente; un admin de Muevo la aprueba (crea holding, compañía, primer
warehouse y administrador) o la rechaza con un motivo.
"""

from alembic import op
import sqlalchemy as sa

revision      = '035_company_applications'
down_revision = '034_transport_applications'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS company_applications (
            id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            status                    VARCHAR(20)  NOT NULL DEFAULT 'pending',
            company_name              VARCHAR(255) NOT NULL,
            legal_name                VARCHAR(255) NOT NULL,
            tax_id                    VARCHAR(50)  NOT NULL,
            industry                  VARCHAR(100),
            allowed_modes             VARCHAR[]    NOT NULL DEFAULT '{}',
            estimated_monthly_stops   INTEGER,
            contact_name              VARCHAR(255) NOT NULL,
            contact_email             VARCHAR(255) NOT NULL,
            contact_phone             VARCHAR(30)  NOT NULL,
            address_line1             VARCHAR(255) NOT NULL,
            city                      VARCHAR(100) NOT NULL,
            state                     VARCHAR(50)  NOT NULL,
            zip_code                  VARCHAR(20),
            warehouse_name            VARCHAR(255) NOT NULL,
            warehouse_address_line1   VARCHAR(255) NOT NULL,
            warehouse_city            VARCHAR(100) NOT NULL,
            warehouse_state           VARCHAR(50)  NOT NULL,
            warehouse_zip_code        VARCHAR(20),
            warehouse_docks_count     INTEGER      NOT NULL DEFAULT 1,
            admin_full_name           VARCHAR(255) NOT NULL,
            admin_email               VARCHAR(255) NOT NULL,
            admin_password_hash       VARCHAR(255) NOT NULL,
            reviewed_by_id            UUID REFERENCES users(id),
            reviewed_at               TIMESTAMPTZ,
            rejection_reason          TEXT,
            company_id                UUID REFERENCES companies(id),
            created_at                TIMESTAMPTZ DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_company_applications_status ON company_applications(status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_company_applications_admin_email ON company_applications(admin_email)"))


def downgrade() -> None:
    op.get_bind().execute(sa.text("DROP TABLE IF EXISTS company_applications CASCADE"))
