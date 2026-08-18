"""add per-company CSV upload limits

Revision ID: 024_company_csv_limits
Revises: 023_service_type_unit_rates
Create Date: 2026-08-10

Permite configurar el límite de filas y de tamaño de archivo para la carga
de CSV por compañía, en vez de una constante global fija. NULL en cualquiera
de las dos columnas significa "usar el default global del sistema" (ver
DEFAULT_MAX_CSV_ROWS / DEFAULT_MAX_CSV_FILE_SIZE_MB en main.py) — así que
esta migración no rompe compañías existentes, simplemente heredan el default
hasta que un admin las configure explícitamente.
"""

from alembic import op
import sqlalchemy as sa

revision      = '024_company_csv_limits'
down_revision = '023_service_type_unit_rates'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS max_csv_rows         INTEGER,
            ADD COLUMN IF NOT EXISTS max_csv_file_size_mb  INTEGER
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE companies
            DROP COLUMN IF EXISTS max_csv_rows,
            DROP COLUMN IF EXISTS max_csv_file_size_mb
    """))
