"""geography: countries, states, cities

Revision ID: 032_geography
Revises: 031_driver_per_route
Create Date: 2026-10-04

Jerarquía país > estado > ciudad para armar rutas por zona. cities.zip_codes
permite resolver la ciudad de una parada a partir de su código postal.
route_details y warehouses reciben city_id (nullable); los textos city/state
actuales se conservan.
"""

from alembic import op
import sqlalchemy as sa

revision      = '032_geography'
down_revision = '031_driver_per_route'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS countries (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            iso2        VARCHAR(2)   NOT NULL UNIQUE,
            iso3        VARCHAR(3)   NOT NULL UNIQUE,
            name        VARCHAR(120) NOT NULL,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE
        )
    """))
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS states (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            country_id  UUID NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            code        VARCHAR(10)  NOT NULL,
            name        VARCHAR(120) NOT NULL,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            CONSTRAINT uq_state_country_code UNIQUE (country_id, code)
        )
    """))
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS cities (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            state_id         UUID NOT NULL REFERENCES states(id) ON DELETE CASCADE,
            name             VARCHAR(120) NOT NULL,
            name_normalized  VARCHAR(120) NOT NULL,
            lat              DOUBLE PRECISION,
            lng              DOUBLE PRECISION,
            zip_codes        VARCHAR(10)[] NOT NULL DEFAULT '{}',
            is_active        BOOLEAN NOT NULL DEFAULT TRUE,
            CONSTRAINT uq_city_state_name UNIQUE (state_id, name_normalized)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_states_country_id ON states(country_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_cities_state_id ON cities(state_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_cities_name_normalized ON cities(name_normalized)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_cities_zip_codes ON cities USING GIN (zip_codes)"))

    conn.execute(sa.text(
        "ALTER TABLE route_details ADD COLUMN IF NOT EXISTS city_id UUID REFERENCES cities(id) ON DELETE SET NULL"
    ))
    conn.execute(sa.text(
        "ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS city_id UUID REFERENCES cities(id) ON DELETE SET NULL"
    ))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_route_details_city_id ON route_details(city_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_warehouses_city_id ON warehouses(city_id)"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_warehouses_city_id"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_route_details_city_id"))
    conn.execute(sa.text("ALTER TABLE warehouses DROP COLUMN IF EXISTS city_id"))
    conn.execute(sa.text("ALTER TABLE route_details DROP COLUMN IF EXISTS city_id"))
    conn.execute(sa.text("DROP TABLE IF EXISTS cities CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS states CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS countries CASCADE"))
