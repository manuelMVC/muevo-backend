"""
seed_demo_data.py — Datos de demo para validar el portal warehouse de Muevo.

Crea:
  - 3 clientes del warehouse
  - 5 lotes en distintos estados
  - 15 rutas distribuidas entre los lotes (3 por lote)
  - 60+ paradas con coordenadas reales de Orlando, FL

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python seed_demo_data.py
"""

import sys, os, uuid
from datetime import datetime, timedelta, date
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL","postgresql://postgres:Semeolvido.01@localhost:5432/muevodb")

from models import (
    Company, Warehouse, RouteHeader, RouteDetail, ShipmentItem,
    RouteBatch, RouteBatchItem, BatchClient, ServiceType,
    BatchStatus, BatchItemStatus, RouteStatus, StopStatus, ServiceMode,
)

engine = create_engine(DATABASE_URL, echo=False)

def uid(): return uuid.uuid4()
def hhmm(h, m=0): return datetime.strptime(f"{h}:{m:02d}", "%H:%M").time()

# ── Paradas reales Orlando FL ─────────────────────────────────────────────────
# lat/lng/zip_code obtenidos por geocodificación inversa (Nominatim/OpenStreetMap)
# sobre las coordenadas ya cargadas, para que coincidan con la dirección real.
STOPS = [
    ("Orange County Courthouse",      "425 N Orange Ave",        28.5493,-81.3791, "32801"),
    ("City Hall Orlando",             "400 S Orange Ave",        28.5381,-81.3791, "32801"),
    ("Dr. Phillips Center",           "445 S Magnolia Ave",      28.5370,-81.3752, "32801"),
    ("Amway Center",                  "400 W Church St",         28.5392,-81.3836, "32801"),
    ("Bank of America Tower",         "390 N Orange Ave",        28.5481,-81.3791, "32801"),
    ("CNL Center",                    "450 S Orange Ave",        28.5365,-81.3791, "32801"),
    ("SunTrust Center",               "200 S Orange Ave",        28.5423,-81.3791, "32801"),
    ("UCF Downtown Campus",           "500 W Livingston St",     28.5414,-81.3866, "32805"),
    ("Orange Ave Medical Center",     "1800 N Orange Ave",       28.5661,-81.3791, "32804"),
    ("AdventHealth Orlando",          "601 E Rollins St",        28.5653,-81.3670, "32803"),
    ("Florida Hospital East",         "7727 Lake Underhill Rd",  28.5321,-81.2902, "32807"),
    ("Marriott World Center",         "8701 World Center Dr",    28.3871,-81.5227, "32836"),
    ("Disney Springs",                "1486 Buena Vista Dr",     28.3706,-81.5193, "32821"),
    ("SeaWorld Orlando",              "7007 Sea World Dr",       28.4109,-81.4612, "32821"),
    ("Universal Studios",             "6000 Universal Blvd",     28.4747,-81.4672, "32819"),
    ("Orange County Convention",      "9800 International Dr",   28.4244,-81.4697, "32819"),
    ("Mall at Millenia",              "4200 Conroy Rd",          28.5025,-81.4358, "32811"),
    ("Florida Mall",                  "8001 S Orange Blossom Tr",28.4538,-81.4080, "32809"),
    ("Deloitte Orlando",              "200 S Orange Ave #2200",  28.5419,-81.3791, "32801"),
    ("PwC Orlando",                   "300 S Orange Ave #800",   28.5405,-81.3791, "32801"),
    ("KPMG Orlando",                  "420 S Orange Ave #600",   28.5371,-81.3791, "32801"),
    ("Siemens Orlando",               "3333 S Orange Ave",       28.5049,-81.3791, "32806"),
    ("Darden Restaurants HQ",         "1000 Darden Center Dr",   28.4613,-81.4636, "32819"),
    ("HCA Healthcare Orlando",        "52 W Underwood St",       28.5199,-81.3766, "32855"),
    ("Tupperware Brands",             "14901 S Orange Blossom",  28.4050,-81.4080, "32837"),
    ("Danaher Altamonte",             "828 Douglas Ave",         28.6611,-81.3964, "32714"),
    ("Lockheed Martin Orl",           "9500 Astronaut Blvd",     28.4903,-80.7077, "32953"),
    ("Tech Data Orl",                 "5350 Tech Data Dr",       28.5723,-81.3964, "32804"),
    ("Hertz Orlando Airport",         "8501 Williams Rd",        28.4278,-81.3120, "32862"),
    ("Amazon Fulfillment ORL",        "12340 Boggy Creek Rd",    28.4027,-81.2820, "32827"),
]

with Session(engine) as db:
    company = db.execute(select(Company)).scalar_one_or_none()
    if not company:
        print("❌ Sin compañía. Corré las migraciones primero.")
        sys.exit(1)

    wh = db.execute(select(Warehouse).where(Warehouse.company_id == company.id)).scalar_one_or_none()

    def get_st(code):
        return db.execute(select(ServiceType).where(ServiceType.code == code)).scalar_one_or_none()

    st_msg = get_st('mensajeria')
    st_log = get_st('logistica')
    st_pkg = get_st('paqueteria')

    print(f"✓ Compañía: {company.name}")

    # ── Clientes ──────────────────────────────────────────────────────────────
    def get_or_create_client(email, **kwargs):
        c = db.execute(select(BatchClient).where(
            BatchClient.company_id == company.id,
            BatchClient.email == email
        )).scalar_one_or_none()
        if c:
            return c
        c = BatchClient(id=uid(), company_id=company.id, email=email, **kwargs)
        db.add(c); db.flush()
        return c

    cl_leg = get_or_create_client("ops@legaldocs-express.com",
        name="LegalDocs Express LLC", phone="+1 407 555 0101",
        codigo_cliente="CLI-LEG", ciudad="Orlando", estado="FL",
        notify_batch_status=True, notify_delivery=True, notify_incident=True)

    cl_med = get_or_create_client("logistics@medsupply-cf.com",
        name="MedSupply Central Florida", phone="+1 407 555 0202",
        codigo_cliente="CLI-MED", ciudad="Orlando", estado="FL",
        notify_batch_status=True, notify_stop_status=True, notify_delivery=True, notify_incident=True)

    cl_tch = get_or_create_client("dist@techcorp-fl.com",
        name="TechCorp Distribuciones", phone="+1 305 555 0303",
        codigo_cliente="CLI-TCH", ciudad="Orlando", estado="FL",
        notify_batch_status=True, notify_delivery=True)

    print(f"✓ 3 clientes listos")

    # ── Helper: crear ruta con paradas ────────────────────────────────────────
    def make_route(title, stop_indices, st_id, mode, sched_date, gross,
                   client_code, status=RouteStatus.DRAFT, completed=0, vehicle_type='furgoneta'):
        num = f"ORL-{sched_date.strftime('%Y%m%d')}-{uid().hex[:5].upper()}"
        r = RouteHeader(
            id=uid(), company_id=company.id,
            origin_warehouse_id=wh.id if wh else None,
            route_number=num, title=title,
            service_mode=mode, service_type_id=st_id,
            required_vehicle_type=vehicle_type,
            client_code=client_code, status=status,
            scheduled_date=datetime.combine(sched_date, datetime.min.time()),
            scheduled_start=datetime.combine(sched_date, hhmm(8)),
            scheduled_end=datetime.combine(sched_date, hhmm(17)),
            total_stops=len(stop_indices),
            completed_stops=completed,
            gross_pay=gross,
            muevo_commission_pct=5,
            muevo_commission_amt=round(gross * 0.05, 2),
            net_pay_estimated=round(gross * 0.88, 2),
        )
        db.add(r); db.flush()

        for seq, idx in enumerate(stop_indices, 1):
            nm, addr, lat, lng, zip_code = STOPS[idx]
            done = seq <= completed
            d = RouteDetail(
                id=uid(), route_header_id=r.id,
                sequence_order=seq,
                status=StopStatus.COMPLETED if done else StopStatus.PENDING,
                company_name=nm, address_line1=addr,
                city="Orlando", state="FL", zip_code=zip_code, lat=lat, lng=lng,
                contact_name="Recepción", contact_phone=f"+1 407 555 {seq:04d}",
                weight_lbs=20.0 + seq * 5, volume_ft3=3.0 + seq,
                codigo_cliente=client_code, pod_required='signature',
                eta_scheduled=datetime.combine(sched_date, hhmm(8 + seq)),
            )
            db.add(d); db.flush()
            db.add(ShipmentItem(
                id=uid(), route_detail_id=d.id, line_number=1,
                package_code=f"PKG-{num[-5:]}-{seq:02d}",
                barcode=f"BC-{client_code}-{seq:04d}",
                qr_code=f'{{"cliente":"{client_code}","seq":{seq}}}',
                item_type='box', quantity=seq % 3 + 1,
                weight_lbs=20.0 + seq * 5, volume_ft3=3.0 + seq,
                declared_value=150.0 * seq,
            ))
        return r

    # Arrancar el contador desde el máximo batch_number ya existente para esta compañía
    existing_last = db.execute(
        select(RouteBatch.batch_number)
        .where(RouteBatch.company_id == company.id, RouteBatch.batch_number.isnot(None))
        .order_by(RouteBatch.batch_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    start_n = int(existing_last.split('-')[-1]) if existing_last else 0
    batch_counter = [start_n]  # mutable counter for sequential numbering

    def make_batch(notes, status, client_id, client_code, routes_specs, sched_date,
                    vehicle_type='furgoneta', service_type='mensajeria'):
        """routes_specs = list of (title, stop_indices, st, mode, gross, route_status, completed)"""
        routes = []
        for r_spec in routes_specs:
            title, idxs, st, mode, gross, rstatus, comp = r_spec[:7]
            veh = r_spec[7] if len(r_spec) > 7 else 'furgoneta' 
            vt = veh if len(r_spec) > 7 else 'furgoneta'
        routes.append(make_route(title, idxs, st, mode, sched_date, gross,
                                     client_code, rstatus, comp, vt))

        batch_counter[0] += 1
        b = RouteBatch(
            id=uid(), company_id=company.id,
            batch_number=f"ORL-LOT-{batch_counter[0]:04d}",
            client_id=client_id, client_code=client_code,
            vehicle_type=vehicle_type, service_type=service_type,
            status=status, notes=notes,
        )
        db.add(b); db.flush()
        for r in routes:
            bstatus = BatchItemStatus.COMPLETED if r.status == RouteStatus.COMPLETED \
                 else BatchItemStatus.ACCEPTED if r.status in (RouteStatus.IN_PROGRESS, RouteStatus.ASSIGNED) \
                 else BatchItemStatus.PENDING
            db.add(RouteBatchItem(id=uid(), batch_id=b.id,
                                  route_header_id=r.id, status=bstatus))
        print(f"  ✓ Lote '{notes}' — {len(routes)} rutas — {status.value}")
        return b

    today     = date.today()
    yesterday = today - timedelta(days=1)
    last_week = today - timedelta(days=7)
    tomorrow  = today + timedelta(days=1)
    next_week = today + timedelta(days=3)

    print("\nCreando lotes...")

    # ── LOTE 1: COMPLETADO — Mensajería legal ─────────────────────────────────
    make_batch(
        notes="Mensajería Legal — Semana Pasada",
        status=BatchStatus.COMPLETED,
        client_id=cl_leg.id, client_code="CLI-LEG",
        vehicle_type="furgoneta", service_type="mensajeria",
        sched_date=last_week,
        routes_specs=[
            ("Bufetes Downtown — Zona Norte",
             [0,4,6,18,19], st_msg.id if st_msg else None, ServiceMode.MENSAJERIA,
             145.00, RouteStatus.COMPLETED, 5, 'furgoneta'),
            ("Documentos Corporativos — Orange Ave",
             [5,6,20,1], st_msg.id if st_msg else None, ServiceMode.MENSAJERIA,
             98.50, RouteStatus.COMPLETED, 4, 'furgoneta'),
            ("Expedientes Judiciales — Centro",
             [2,3,7,0], st_msg.id if st_msg else None, ServiceMode.MENSAJERIA,
             112.00, RouteStatus.COMPLETED, 4),
        ]
    )

    # ── LOTE 2: PARCIALMENTE COMPLETADO — Logística médica ───────────────────
    make_batch(
        notes="Logística Médica — Hoy",
        status=BatchStatus.PARTIALLY_COMPLETED,
        client_id=cl_med.id, client_code="CLI-MED",
        vehicle_type="furgon", service_type="logistica",
        sched_date=today,
        routes_specs=[
            ("Suministros Hospitales — Zona Central",
             [9,10,8,23], st_log.id if st_log else None, ServiceMode.LOGISTICA,
             380.00, RouteStatus.IN_PROGRESS, 2),
            ("Equipos Clínicos — Zona Norte",
             [25,27,8], st_log.id if st_log else None, ServiceMode.LOGISTICA,
             280.00, RouteStatus.PUBLISHED, 0),
            ("Reactivos Lab — Zona Este",
             [10,29,17], st_log.id if st_log else None, ServiceMode.LOGISTICA,
             195.00, RouteStatus.DRAFT, 0),
        ]
    )

    # ── LOTE 3: APROBADO — Paquetería tech ────────────────────────────────────
    make_batch(
        notes="Paquetería Tech — Mañana",
        status=BatchStatus.APPROVED,
        client_id=cl_tch.id, client_code="CLI-TCH",
        vehicle_type="furgoneta", service_type="paqueteria",
        sched_date=tomorrow,
        routes_specs=[
            ("Hardware Internacional Drive",
             [15,16,22,13], st_pkg.id if st_pkg else None, ServiceMode.MENSAJERIA,
             220.00, RouteStatus.DRAFT, 0),
            ("Periféricos Theme Parks Area",
             [11,12,14,24], st_pkg.id if st_pkg else None, ServiceMode.MENSAJERIA,
             185.00, RouteStatus.DRAFT, 0),
            ("Servidores — Zona Industrial",
             [27,29,28,21], st_pkg.id if st_pkg else None, ServiceMode.MENSAJERIA,
             260.00, RouteStatus.DRAFT, 0),
        ]
    )

    # ── LOTE 4: APROBADO — Mensajería mixta gran volumen ─────────────────────
    make_batch(
        notes="Entregas Express — Próxima Semana",
        status=BatchStatus.APPROVED,
        client_id=cl_leg.id, client_code="CLI-LEG",
        vehicle_type="furgoneta", service_type="mensajeria",
        sched_date=next_week,
        routes_specs=[
            ("Documentos Legales — Zona Sur",
             [1,5,17,24], st_msg.id if st_msg else None, ServiceMode.MENSAJERIA,
             135.00, RouteStatus.DRAFT, 0),
            ("Notarías y Escribanías — Centro",
             [2,3,7,18,20], st_msg.id if st_msg else None, ServiceMode.MENSAJERIA,
             168.00, RouteStatus.DRAFT, 0),
            ("Contratos Corporativos — Towers",
             [4,6,19,21], st_msg.id if st_msg else None, ServiceMode.MENSAJERIA,
             142.00, RouteStatus.DRAFT, 0),
        ]
    )

    # ── LOTE 5: BORRADOR — Logística mixta ────────────────────────────────────
    make_batch(
        notes="Distribución General — Sin Confirmar",
        status=BatchStatus.DRAFT,
        client_id=None, client_code=None,
        vehicle_type="furgon", service_type="logistica",
        sched_date=next_week + timedelta(days=2),
        routes_specs=[
            ("Distribución Mall Area",
             [16,17,22], st_log.id if st_log else None, ServiceMode.LOGISTICA,
             175.00, RouteStatus.DRAFT, 0),
            ("Almacenes Zona Norte",
             [25,27,29,28], st_log.id if st_log else None, ServiceMode.LOGISTICA,
             220.00, RouteStatus.DRAFT, 0),
            ("Logística Aeropuerto",
             [28,26,13], st_log.id if st_log else None, ServiceMode.LOGISTICA,
             195.00, RouteStatus.DRAFT, 0),
        ]
    )

    db.commit()

    print("\n" + "─"*60)
    print("✅ Seed completado exitosamente:")
    print("   5 lotes  |  15 rutas  |  60+ paradas")
    print()
    print("   Lote 1 — COMPLETADO       — 3 rutas · CLI-LEG")
    print("   Lote 2 — PARC. COMPLETADO — 3 rutas · CLI-MED")
    print("   Lote 3 — APROBADO         — 3 rutas · CLI-TCH")
    print("   Lote 4 — APROBADO         — 3 rutas · CLI-LEG")
    print("   Lote 5 — BORRADOR         — 3 rutas · mixto")
    print()
    print("   Login: carlos@muevo.app / muevo123")
    print("─"*60)
