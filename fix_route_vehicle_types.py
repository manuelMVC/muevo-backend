"""
fix_route_vehicle_types.py — Copia el vehicle_type del lote a todas sus rutas.

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python fix_route_vehicle_types.py
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
    # Diagnóstico
    batches = db.execute(
        select(RouteBatch).where(RouteBatch.vehicle_type != None)
    ).scalars().all()

    print(f"Lotes con vehicle_type: {len(batches)}")
    for b in batches[:5]:
        items = db.execute(
            select(RouteBatchItem).where(RouteBatchItem.batch_id == b.id)
        ).scalars().all()
        print(f"  Lote {(b.notes or '')[:30]:32} vt={b.vehicle_type} — {len(items)} rutas")
        for item in items:
            r = db.get(RouteHeader, item.route_header_id)
            if r:
                print(f"    Ruta {r.route_number:30} required_vt={r.required_vehicle_type}")

    # Fix
    fixed_routes = 0
    for b in batches:
        items = db.execute(
            select(RouteBatchItem).where(RouteBatchItem.batch_id == b.id)
        ).scalars().all()

        for item in items:
            route = db.get(RouteHeader, item.route_header_id)
            if route and route.required_vehicle_type != b.vehicle_type:
                old = route.required_vehicle_type or 'NULL'
                route.required_vehicle_type = b.vehicle_type
                fixed_routes += 1
                print(f"  ✓ {route.route_number:35} {old:12} → {b.vehicle_type}")

    db.commit()
    print(f"\n✅ {fixed_routes} ruta(s) actualizadas con el tipo de vehículo de su lote.")
