"""company service types

Revision ID: 036_company_service_types
Revises: 035_company_applications
Create Date: 2026-10-22

companies.allowed_modes pasa a guardar códigos del catálogo service_types (uno o varios) y a
validar el armado de lotes. Las compañías existentes usaban otro vocabulario (por ejemplo
"empleados", que en el catálogo es "personas"), así que se normalizan y se les suman los
servicios que ya usan en sus lotes para no bloquear nada.
"""

from alembic import op
import sqlalchemy as sa

revision      = '036_company_service_types'
down_revision = '035_company_applications'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.get_bind().execute(sa.text("""
        UPDATE companies c SET allowed_modes = COALESCE((
            SELECT array_agg(DISTINCT code ORDER BY code) FROM (
                SELECT CASE m WHEN 'empleados' THEN 'personas' ELSE m END AS code
                FROM unnest(c.allowed_modes) AS m
                UNION
                SELECT b.service_type FROM route_batches b
                WHERE b.company_id = c.id AND b.service_type IS NOT NULL
            ) x WHERE code IN (SELECT code FROM service_types)
        ), '{}')
    """))


def downgrade() -> None:
    pass  # migración de datos: no hay forma de recuperar el contenido anterior
