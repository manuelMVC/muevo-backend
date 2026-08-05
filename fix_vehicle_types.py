"""
fix_vehicle_types.py — Completa el campo required_vehicle_type en rutas que lo tienen vacío.

Lógica:
  - Si el título/modo de la ruta sugiere el tipo → asigna ese tipo
  - Sino → asigna 'furgoneta' como default

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python fix_vehicle_types.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Semeolvido.01@localhost:5432/muevodb")

from models import RouteHeader, ServiceMode

engine = create_engine(DATABASE_URL, echo=False)

def infer_vehicle_type(route: RouteHeader) -> str:
    """Infiere el tipo de vehículo según el modo de servicio y el título."""
    mode  = route.service_mode
    title = (route.title or '').lower()

    if mode == ServiceMode.MENSAJERIA:
        if any(w in title for w in ['sobre', 'documento', 'expediente', 'legal', 'carta', 'bufete']):
            return 'moto'
        return 'furgoneta'

    if mode == ServiceMode.LOGISTICA:
        if any(w in title for w in ['pesado', 'palet', 'pallet', 'almacen', 'industrial']):
            return 'furgon'
        return 'furgoneta'

    if mode == ServiceMode.EMPLEADOS:
        return 'sedan'

    if mode == ServiceMode.PAQUETERIA:
        return 'furgoneta'

    # MIXTO y cualquier otro
    return 'furgoneta'

with Session(engine) as db:
    # Buscar rutas sin vehicle_type (solo NULL — es un enum, no puede ser string vacío)
    routes = db.execute(
        select(RouteHeader).where(
            RouteHeader.required_vehicle_type == None
        )
    ).scalars().all()

    if not routes:
        print("✅ Todas las rutas ya tienen tipo de vehículo asignado.")
    else:
        print(f"🔧 {len(routes)} ruta(s) sin tipo de vehículo — corrigiendo...\n")
        counts = {}
        for r in routes:
            vt = infer_vehicle_type(r)
            r.required_vehicle_type = vt
            counts[vt] = counts.get(vt, 0) + 1
            print(f"  ✓ {r.route_number:35} [{r.service_mode.value if hasattr(r.service_mode,'value') else r.service_mode}] → {vt}")

        db.commit()
        print(f"\n✅ {len(routes)} rutas actualizadas:")
        for vt, n in counts.items():
            print(f"   {vt}: {n}")
