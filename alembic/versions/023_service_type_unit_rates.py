"""add per-unit rates to service_types for automatic price suggestion

Revision ID: 023_service_type_unit_rates
Revises: 022_price_negotiation
Create Date: 2026-08-09

Agrega tarifas por unidad al catálogo de tipos de servicio, para que el
precio inicial de una ruta pueda sugerirse automáticamente en base a
distancia + peso + volumen (ver calculate_suggested_price() en main.py):
  - precio_por_km:  costo adicional por km estimado de la ruta
  - precio_por_lb:  costo adicional por libra de carga
  - precio_por_ft3: costo adicional por pie cúbico de carga

Los valores de arranque son estimaciones razonables (no tarifas oficiales)
pensadas para que la sugerencia caiga dentro del rango
[importe_minimo, importe_maximo] de cada tipo de servicio en rutas típicas.
Se pueden ajustar libremente vía UPDATE — no hay lógica de negocio que
dependa de valores específicos.
"""

from alembic import op
import sqlalchemy as sa

revision      = '023_service_type_unit_rates'
down_revision = '022_price_negotiation'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        ALTER TABLE service_types
            ADD COLUMN IF NOT EXISTS precio_por_km  NUMERIC(8,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS precio_por_lb  NUMERIC(8,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS precio_por_ft3 NUMERIC(8,2) NOT NULL DEFAULT 0
    """))

    # Tarifas de arranque por tipo de servicio.
    # "personas" (transporte de empleados) no factura por peso/volumen de carga.
    rates = {
        'mensajeria': (0.50, 0.05, 0.30),
        'paqueteria': (0.60, 0.08, 0.40),
        'logistica':  (1.20, 0.03, 0.20),
        'personas':   (0.70, 0.00, 0.00),
    }
    for code, (km, lb, ft3) in rates.items():
        conn.execute(sa.text(
            "UPDATE service_types SET precio_por_km=:km, precio_por_lb=:lb, precio_por_ft3=:ft3 WHERE code=:code"
        ), {"km": km, "lb": lb, "ft3": ft3, "code": code})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE service_types
            DROP COLUMN IF EXISTS precio_por_km,
            DROP COLUMN IF EXISTS precio_por_lb,
            DROP COLUMN IF EXISTS precio_por_ft3
    """))
