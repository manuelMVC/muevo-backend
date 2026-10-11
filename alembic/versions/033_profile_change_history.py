"""profile change history

Revision ID: 033_profile_change_history
Revises: 032_geography
Create Date: 2026-10-06

Historial legible de cambios de perfiles y de los usuarios de esos perfiles.
Una fila por cada cambio: quién, cuándo, qué cambió, valor anterior y valor actual.

Cubre: profiles, profile_permissions, holding_user_profiles, holding_users y
holding_user_companies. La captura es automática (listener de SQLAlchemy en
models.py), no depende de que cada endpoint se acuerde de registrar.

La tabla es de solo lectura: un trigger rechaza UPDATE, DELETE y TRUNCATE, así
que el historial no se puede reescribir desde la aplicación ni por accidente.
No lleva llaves foráneas a propósito: el historial debe sobrevivir aunque se
borre el perfil o el usuario al que se refiere (por eso guarda copias del
código, el nombre y el correo).
"""

from alembic import op
import sqlalchemy as sa

revision      = '033_profile_change_history'
down_revision = '032_geography'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_change_history (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            seq                   BIGINT GENERATED ALWAYS AS IDENTITY,
            changed_at            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            changed_by_user_id    UUID,
            changed_by_name       VARCHAR(255),
            changed_by_email      VARCHAR(255),
            source                VARCHAR(10) NOT NULL DEFAULT 'api',
            entity_type           VARCHAR(30) NOT NULL,
            change_type           VARCHAR(40) NOT NULL,
            description           TEXT NOT NULL,
            field_name            VARCHAR(60),
            old_value             TEXT,
            new_value             TEXT,
            profile_id            UUID,
            profile_code          VARCHAR(60),
            target_holding_user_id UUID,
            target_user_id        UUID,
            target_user_email     VARCHAR(255),
            company_id            UUID,
            company_name          VARCHAR(255),
            record_id             VARCHAR(100),
            details               JSONB
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pch_changed_at ON profile_change_history(changed_at)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pch_seq ON profile_change_history(seq)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pch_profile_id ON profile_change_history(profile_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pch_target_user_id ON profile_change_history(target_user_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pch_changed_by ON profile_change_history(changed_by_user_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pch_entity ON profile_change_history(entity_type, change_type)"))

    # Solo lectura: nada de UPDATE, DELETE ni TRUNCATE
    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION fn_profile_change_history_immutable() RETURNS trigger AS $fn$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'profile_change_history es de solo lectura: no se permite ' || TG_OP || ' sobre el historial';
        END;
        $fn$ LANGUAGE plpgsql
    """))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_profile_change_history_rows ON profile_change_history"))
    conn.execute(sa.text("""
        CREATE TRIGGER trg_profile_change_history_rows
        BEFORE UPDATE OR DELETE ON profile_change_history
        FOR EACH ROW EXECUTE FUNCTION fn_profile_change_history_immutable()
    """))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_profile_change_history_truncate ON profile_change_history"))
    conn.execute(sa.text("""
        CREATE TRIGGER trg_profile_change_history_truncate
        BEFORE TRUNCATE ON profile_change_history
        FOR EACH STATEMENT EXECUTE FUNCTION fn_profile_change_history_immutable()
    """))


def downgrade() -> None:
    conn = op.get_bind()
    # El trigger impide borrar filas, pero DROP TABLE no lo dispara.
    conn.execute(sa.text("DROP TABLE IF EXISTS profile_change_history"))
    conn.execute(sa.text("DROP FUNCTION IF EXISTS fn_profile_change_history_immutable()"))
