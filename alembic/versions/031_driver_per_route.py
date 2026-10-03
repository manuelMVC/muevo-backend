"""driver per route

Revision ID: 031_driver_per_route
Revises: 030_notifications
Create Date: 2026-10-05

El conductor deja de colgar del vehículo (vehicles.driver_id) y pasa a asignarse
por ruta (route_headers.driver_id). Como el conductor perdía así su único vínculo
con la empresa de transporte, se agrega drivers.transport_company_id.

Los datos se migran antes de borrar la columna:
  - drivers.transport_company_id  <- empresa del vehículo que tenía asignado
  - route_headers.driver_id       <- conductor del vehículo de la ruta
"""

from alembic import op
import sqlalchemy as sa

revision      = '031_driver_per_route'
down_revision = '030_notifications'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS transport_company_id UUID REFERENCES transport_companies(id)
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_drivers_transport_company_id ON drivers(transport_company_id)"
    ))
    conn.execute(sa.text("""
        ALTER TABLE route_headers
            ADD COLUMN IF NOT EXISTS driver_id UUID REFERENCES drivers(id)
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_route_headers_driver_id ON route_headers(driver_id)"
    ))

    conn.execute(sa.text("""
        UPDATE drivers d SET transport_company_id = v.transport_company_id
        FROM vehicles v
        WHERE v.driver_id = d.id AND v.transport_company_id IS NOT NULL AND d.transport_company_id IS NULL
    """))
    conn.execute(sa.text("""
        UPDATE route_headers rh SET driver_id = v.driver_id
        FROM vehicles v
        WHERE rh.vehicle_id = v.id AND v.driver_id IS NOT NULL AND rh.driver_id IS NULL
    """))

    conn.execute(sa.text("DROP INDEX IF EXISTS ix_vehicles_driver_id"))
    conn.execute(sa.text("ALTER TABLE vehicles DROP COLUMN IF EXISTS driver_id"))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        ALTER TABLE vehicles
            ADD COLUMN IF NOT EXISTS driver_id UUID REFERENCES drivers(id) ON DELETE CASCADE
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_vehicles_driver_id ON vehicles(driver_id)"
    ))
    # Mejor esfuerzo: el conductor de la ruta más reciente de cada vehículo
    conn.execute(sa.text("""
        UPDATE vehicles v SET driver_id = x.driver_id
        FROM (
            SELECT DISTINCT ON (vehicle_id) vehicle_id, driver_id
            FROM route_headers
            WHERE vehicle_id IS NOT NULL AND driver_id IS NOT NULL
            ORDER BY vehicle_id, created_at DESC
        ) x
        WHERE v.id = x.vehicle_id
    """))

    conn.execute(sa.text("DROP INDEX IF EXISTS ix_route_headers_driver_id"))
    conn.execute(sa.text("ALTER TABLE route_headers DROP COLUMN IF EXISTS driver_id"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_drivers_transport_company_id"))
    conn.execute(sa.text("ALTER TABLE drivers DROP COLUMN IF EXISTS transport_company_id"))
