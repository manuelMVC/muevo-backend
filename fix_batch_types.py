"""
fix_batch_types.py — Completa vehicle_type y service_type en lotes existentes.

Toma el valor de la primera ruta del lote (todas deberían tener el mismo
ya que se definen al crear el lote).

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python fix_batch_types.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Semeolvido.01@localhost:5432/muevodb")

from models import RouteBatch, RouteBatchItem, RouteHeader

engine = create_engine(DATABASE_URL, echo=False)

with Session(engine) as db:
    batches = db.execute(
        select(RouteBatch).where(
            (RouteBatch.vehicle_type == None) |
            (RouteBatch.service_type == None)
        )
    ).scalars().all()

    if not batches:
        print("✅ Todos los lotes ya tienen vehicle_type y service_type.")
        sys.exit(0)

    print(f"🔧 {len(batches)} lote(s) sin tipos — corrigiendo...\n")
    fixed = 0

    for b in batches:
        # Obtener primera ruta del lote
        item = db.execute(
            select(RouteBatchItem).where(RouteBatchItem.batch_id == b.id)
        ).scalars().first()

        if not item:
            continue

        route = db.get(RouteHeader, item.route_header_id)
        if not route:
            continue

        # vehicle_type desde la ruta
        if not b.vehicle_type:
            b.vehicle_type = route.required_vehicle_type or 'furgoneta'

        # service_type desde el modo de servicio de la ruta
        if not b.service_type:
            mode = route.service_mode
            b.service_type = mode.value if hasattr(mode, 'value') else (mode or 'mensajeria')

        print(f"  ✓ {(b.notes or str(b.id)[:8]):40} → veh: {b.vehicle_type:12} svc: {b.service_type}")
        fixed += 1

    db.commit()
    print(f"\n✅ {fixed} lotes actualizados.")
