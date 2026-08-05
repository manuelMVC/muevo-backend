"""
seed_incident_types.py — Siembra el catálogo de tipos de incidencia.

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python seed_incident_types.py
"""

import sys, os, uuid
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Semeolvido.01@localhost:5432/muevodb")

from models import IncidentType, IncidentSeverity

engine = create_engine(DATABASE_URL, echo=False)

TIPOS = [
    # ── Críticos ──────────────────────────────────────────────────────────────
    { "code": "vehicle_accident",     "name": "Accidente vehicular",           "severity": IncidentSeverity.CRITICAL, "affects": True  },
    { "code": "cargo_theft",          "name": "Robo de carga",                 "severity": IncidentSeverity.CRITICAL, "affects": True  },
    { "code": "driver_injury",        "name": "Lesión del conductor",           "severity": IncidentSeverity.CRITICAL, "affects": True  },

    # ── Altos ─────────────────────────────────────────────────────────────────
    { "code": "vehicle_breakdown",    "name": "Vehículo descompuesto",         "severity": IncidentSeverity.HIGH,     "affects": True  },
    { "code": "driver_no_show",       "name": "Conductor no se presentó",      "severity": IncidentSeverity.HIGH,     "affects": True  },
    { "code": "route_blocked",        "name": "Ruta bloqueada / inaccesible",  "severity": IncidentSeverity.HIGH,     "affects": True  },
    { "code": "hazmat_spill",         "name": "Derrame de material peligroso", "severity": IncidentSeverity.HIGH,     "affects": True  },

    # ── Medios ────────────────────────────────────────────────────────────────
    { "code": "damaged_package",      "name": "Paquete dañado",                "severity": IncidentSeverity.MEDIUM,   "affects": False },
    { "code": "delivery_refused",     "name": "Entrega rechazada por receptor","severity": IncidentSeverity.MEDIUM,   "affects": False },
    { "code": "wrong_package",        "name": "Paquete incorrecto entregado",  "severity": IncidentSeverity.MEDIUM,   "affects": False },
    { "code": "missing_signature",    "name": "Falta firma de recepción",      "severity": IncidentSeverity.MEDIUM,   "affects": False },
    { "code": "access_denied",        "name": "Acceso denegado al edificio",   "severity": IncidentSeverity.MEDIUM,   "affects": False },
    { "code": "receiver_not_present", "name": "Receptor ausente",              "severity": IncidentSeverity.MEDIUM,   "affects": False },
    { "code": "weather_delay",        "name": "Demora por condiciones climáticas","severity": IncidentSeverity.MEDIUM,"affects": False },

    # ── Bajos ─────────────────────────────────────────────────────────────────
    { "code": "address_not_found",    "name": "Dirección no encontrada",       "severity": IncidentSeverity.LOW,      "affects": False },
    { "code": "traffic_delay",        "name": "Demora por tráfico",            "severity": IncidentSeverity.LOW,      "affects": False },
    { "code": "parking_issue",        "name": "Problema de estacionamiento",   "severity": IncidentSeverity.LOW,      "affects": False },
    { "code": "documentation_error",  "name": "Error en documentación",        "severity": IncidentSeverity.LOW,      "affects": False },
    { "code": "weight_discrepancy",   "name": "Discrepancia de peso",          "severity": IncidentSeverity.LOW,      "affects": False },
    { "code": "other",                "name": "Otro / Sin categoría",          "severity": IncidentSeverity.LOW,      "affects": False },
]

with Session(engine) as db:
    created = 0
    skipped = 0

    for t in TIPOS:
        existing = db.execute(
            select(IncidentType).where(IncidentType.code == t["code"])
        ).scalar_one_or_none()

        if existing:
            skipped += 1
            continue

        db.add(IncidentType(
            id=uuid.uuid4(),
            code=t["code"],
            name=t["name"],
            severity=t["severity"],
            affects_route_status=t["affects"],
            is_active=True,
        ))
        created += 1

    db.commit()

    sev_labels = {
        IncidentSeverity.CRITICAL: "Crítico",
        IncidentSeverity.HIGH:     "Alto",
        IncidentSeverity.MEDIUM:   "Medio",
        IncidentSeverity.LOW:      "Bajo",
    }

    print("\n" + "─"*55)
    print(f"✅ Seed completado: {created} tipos creados · {skipped} ya existían")
    print("─"*55)

    # Mostrar catálogo completo
    todos = db.execute(
        select(IncidentType).order_by(IncidentType.severity, IncidentType.code)
    ).scalars().all()

    current_sev = None
    for t in todos:
        sev = t.severity.value if hasattr(t.severity,'value') else t.severity
        if sev != current_sev:
            current_sev = sev
            print(f"\n  [{sev_labels.get(t.severity, sev).upper()}]")
        afecta = "⚠ afecta ruta" if t.affects_route_status else ""
        print(f"  {'✓' if t.is_active else '○'} {t.code:35} {t.name} {afecta}")

    print(f"\n  Total: {len(todos)} tipos en el catálogo")
    print("─"*55)
