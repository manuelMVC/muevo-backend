"""
fix_assigned_without_transport.py — Corrige rutas en estado ASSIGNED que no
tienen empresa de transporte asignada (inconsistencia de datos).

Estrategia: si la ruta está ASSIGNED pero no tiene transport_company_id,
la regresa a PUBLISHED (estado correcto para "sin transportista todavía").

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python fix_assigned_without_transport.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Semeolvido.01@localhost:5432/muevodb")

from models import RouteHeader, RouteStatus

engine = create_engine(DATABASE_URL, echo=False)

with Session(engine) as db:
    routes = db.execute(
        select(RouteHeader).where(
            RouteHeader.status == RouteStatus.ASSIGNED,
            RouteHeader.transport_company_id == None,
        )
    ).scalars().all()

    if not routes:
        print("✅ No hay rutas ASSIGNED sin transportista.")
        sys.exit(0)

    print(f"🔧 {len(routes)} ruta(s) ASSIGNED sin transportista — corrigiendo a PUBLISHED...\n")

    for r in routes:
        print(f"  ✓ {r.route_number:35} ASSIGNED → PUBLISHED")
        r.status = RouteStatus.PUBLISHED

    db.commit()
    print(f"\n✅ {len(routes)} ruta(s) corregidas.")
