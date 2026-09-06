"""
reset_operational_data.py — Borra los datos operativos de prueba (lotes,
rutas, paradas, ofertas de negociación, clientes, incidencias, recepciones,
auditoría) para poder regenerar un dataset de demo limpio y coherente.

NO toca: holdings/companies/warehouses, usuarios (incluidos los de prueba
por perfil), el modelo de seguridad (modules/permissions/profiles), tarifas
de servicio (service_types), tipos de vehículo/incidencia, ni las empresas
de transporte — eso es configuración del tenant, no "data de prueba".

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python reset_operational_data.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import create_engine, text
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Semeolvido.01@localhost:5432/muevodb")
engine = create_engine(DATABASE_URL, echo=False)

# Orden respetando foreign keys (referencias sin cascade primero, hijos antes que padres)
TABLES_IN_ORDER = [
    "incidents",             # referencia route_headers/route_details/route_batch_items sin cascade
    "reception_items",       # cascade desde receptions, pero se borra explícito igual
    "receptions",            # referencia route_headers sin cascade
    "route_price_offers",    # referencia route_headers (cascade), pero route_headers.pending_offer_id la referencia a ella
    "route_batch_items",
    "shipment_items",
    "route_details",
    "route_headers",
    "route_batches",
    "batch_clients",
    "warehouse_inventory_items",
    "audit_logs",
]


def main():
    print("Borrando datos operativos de prueba...\n")
    with engine.begin() as conn:
        # route_headers.pending_offer_id apunta a route_price_offers — hay que
        # limpiarlo antes de poder borrar esa tabla sin violar la FK.
        conn.execute(text("UPDATE route_headers SET pending_offer_id = NULL"))

        for table in TABLES_IN_ORDER:
            result = conn.execute(text(f"DELETE FROM {table}"))
            print(f"  {table}: {result.rowcount} filas borradas")

    print("\nListo. Se mantuvo intacto: holding/company/warehouse, usuarios, "
          "modelo de seguridad, tarifas, tipos de vehículo/incidencia, "
          "empresas de transporte.")


if __name__ == "__main__":
    main()
