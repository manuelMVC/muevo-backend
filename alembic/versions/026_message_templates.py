"""add message_templates table (catalogo global de mensajes estandar)

Revision ID: 026_message_templates
Revises: 025_receptions
Create Date: 2026-08-17

Catálogo general de mensajes: código + descripción + una macro de texto con
variables `{nombre}` que se sustituyen en tiempo de uso (ver
render_message_template() en main.py). Todavía no está atado a un flujo
específico de la aplicación — es un catálogo de propósito general, mismo
patrón que incident_types (global, is_active en vez de borrado físico).
"""

from alembic import op
import sqlalchemy as sa

revision      = '026_message_templates'
down_revision = '025_receptions'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS message_templates (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code        VARCHAR(60) UNIQUE NOT NULL,
            description VARCHAR(255) NOT NULL,
            macro_text  TEXT NOT NULL,
            is_active   BOOLEAN NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS ix_message_templates_code      ON message_templates(code)",
        "CREATE INDEX IF NOT EXISTS ix_message_templates_is_active ON message_templates(is_active)",
    ]:
        conn.execute(sa.text(idx_sql))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS message_templates CASCADE"))
