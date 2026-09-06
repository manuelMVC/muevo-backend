"""
seed_demo_data.py — Datos de demo para validar los portales warehouse y
transportista de Muevo, coherentes con el modelo de precio/negociación
vigente (septiembre 2026):

  - El precio de cada ruta sale de la fórmula real (calculate_suggested_price
    / _apply_formula_price), nunca hardcodeado.
  - Aprobar un lote abre el marketplace solo (_open_route_marketplace) —
    ninguna ruta queda con precio pero sin negociación abierta si su lote
    ya está aprobado, salvo las de despacho manual (fuera del marketplace
    a propósito, como en producción).
  - Hay 2 empresas de transporte reales pujando en paralelo (Rapid Courier
    Orlando LLC y Sunshine Logistics FL) para poder probar el marketplace
    multi-transportista de punta a punta.

Crea, sobre un dataset previamente vaciado (ver reset_operational_data.py):
  - 3 clientes del warehouse
  - 1 empresa de transporte nueva (Sunshine Logistics FL) + su admin
  - 8 lotes cubriendo cada estado relevante para pruebas manuales:
      1. Borrador — sin aprobar, sin negociación
      2. Aprobado — marketplace recién abierto, todavía sin pujas
      3. Aprobado — pujas activas de 2 transportistas en paralelo
      4. Aprobado — una oferta ya seleccionada, esperando aprobación de holding
      5. Aprobado — ruta ya negociada y publicada (el transportista ganador
         todavía tiene que aceptarla y asignar vehículo)
      6. En curso — despacho manual directo (sin pasar por el marketplace)
      7. Completado — histórico, para facturación/reportes
      8. Cancelado — lote aprobado anulado con motivo

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python seed_demo_data.py
"""

import sys, os, uuid
from datetime import datetime, timedelta, date
sys.path.insert(0, os.path.dirname(__file__))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Semeolvido.01@localhost:5432/muevodb")

from models import (
    Company, Warehouse, User, UserRole, RouteHeader, RouteDetail, ShipmentItem,
    RouteBatch, RouteBatchItem, BatchClient, ServiceType,
    BatchStatus, BatchItemStatus, RouteStatus, StopStatus, ServiceMode,
    TransportCompany, TransportCompanyAdmin, Vehicle,
    RoutePriceOffer, OfferSource, OfferStatus, NegotiationStatus,
)
from main import (
    hash_password, calculate_suggested_price, _apply_formula_price,
    _open_route_marketplace, _close_negotiation, _create_route_detail,
    next_batch_number,
)

engine = create_engine(DATABASE_URL, echo=False)


def uid(): return uuid.uuid4()
def hhmm(h, m=0): return datetime.strptime(f"{h}:{m:02d}", "%H:%M").time()


# ── Paradas reales Orlando FL (lat/lng/zip por geocodificación inversa) ───────
STOPS = [
    ("Orange County Courthouse",      "425 N Orange Ave",        28.5493, -81.3791, "32801"),
    ("City Hall Orlando",             "400 S Orange Ave",        28.5381, -81.3791, "32801"),
    ("Dr. Phillips Center",           "445 S Magnolia Ave",      28.5370, -81.3752, "32801"),
    ("Amway Center",                  "400 W Church St",         28.5392, -81.3836, "32801"),
    ("Bank of America Tower",         "390 N Orange Ave",        28.5481, -81.3791, "32801"),
    ("CNL Center",                    "450 S Orange Ave",        28.5365, -81.3791, "32801"),
    ("SunTrust Center",               "200 S Orange Ave",        28.5423, -81.3791, "32801"),
    ("UCF Downtown Campus",           "500 W Livingston St",     28.5414, -81.3866, "32805"),
    ("Orange Ave Medical Center",     "1800 N Orange Ave",       28.5661, -81.3791, "32804"),
    ("AdventHealth Orlando",          "601 E Rollins St",        28.5653, -81.3670, "32803"),
    ("Florida Hospital East",         "7727 Lake Underhill Rd",  28.5321, -81.2902, "32807"),
    ("Marriott World Center",         "8701 World Center Dr",    28.3871, -81.5227, "32836"),
    ("Disney Springs",                "1486 Buena Vista Dr",     28.3706, -81.5193, "32821"),
    ("SeaWorld Orlando",              "7007 Sea World Dr",       28.4109, -81.4612, "32821"),
    ("Universal Studios",             "6000 Universal Blvd",     28.4747, -81.4672, "32819"),
    ("Orange County Convention",      "9800 International Dr",   28.4244, -81.4697, "32819"),
    ("Mall at Millenia",              "4200 Conroy Rd",          28.5025, -81.4358, "32811"),
    ("Florida Mall",                  "8001 S Orange Blossom Tr", 28.4538, -81.4080, "32809"),
    ("Deloitte Orlando",              "200 S Orange Ave #2200",  28.5419, -81.3791, "32801"),
    ("PwC Orlando",                   "300 S Orange Ave #800",   28.5405, -81.3791, "32801"),
    ("KPMG Orlando",                  "420 S Orange Ave #600",   28.5371, -81.3791, "32801"),
    ("Siemens Orlando",               "3333 S Orange Ave",       28.5049, -81.3791, "32806"),
    ("Darden Restaurants HQ",         "1000 Darden Center Dr",   28.4613, -81.4636, "32819"),
    ("HCA Healthcare Orlando",        "52 W Underwood St",       28.5199, -81.3766, "32855"),
    ("Tupperware Brands",             "14901 S Orange Blossom",  28.4050, -81.4080, "32837"),
    ("Danaher Altamonte",             "828 Douglas Ave",         28.6611, -81.3964, "32714"),
    ("Tech Data Orl",                 "5350 Tech Data Dr",       28.5723, -81.3964, "32804"),
    ("Amazon Fulfillment ORL",        "12340 Boggy Creek Rd",    28.4027, -81.2820, "32827"),
]


def stop_dict(idx, client_code):
    name, addr, lat, lng, zip_code = STOPS[idx]
    return {
        "destino": name, "direccion": addr, "lat": lat, "lng": lng,
        "codigo_postal": zip_code, "codigo_cliente": client_code,
        "contacto": "Recepción", "telefono": "+1 407 555 0100",
        "peso_lbs": 15.0 + idx * 3, "volumen_ft3": 2.0 + idx * 0.4,
    }


with Session(engine) as db:
    company = db.execute(select(Company)).scalar_one_or_none()
    if not company:
        print("❌ Sin compañía. Corré seed_initial_data.py primero.")
        sys.exit(1)
    wh = db.execute(select(Warehouse).where(Warehouse.company_id == company.id)).scalar_one_or_none()
    carlos = db.execute(select(User).where(User.email == "carlos@muevo.app")).scalar_one_or_none()
    super_admin = db.execute(select(User).where(User.email == "super.admin@muevo.app")).scalar_one_or_none()

    def get_st(code):
        return db.execute(select(ServiceType).where(ServiceType.code == code)).scalar_one_or_none()
    st_msg, st_log, st_pkg = get_st("mensajeria"), get_st("logistica"), get_st("paqueteria")

    print(f"✓ Compañía: {company.name}")

    # ── Clientes ──────────────────────────────────────────────────────────────
    def get_or_create_client(email, **kwargs):
        c = db.execute(select(BatchClient).where(
            BatchClient.company_id == company.id, BatchClient.email == email
        )).scalar_one_or_none()
        if c:
            return c
        c = BatchClient(id=uid(), company_id=company.id, email=email, **kwargs)
        db.add(c); db.flush()
        return c

    cl_leg = get_or_create_client("ops@legaldocs-express.com",
        name="LegalDocs Express LLC", phone="+1 407 555 0101", codigo_cliente="CLI-LEG",
        ciudad="Orlando", estado="FL", notify_batch_status=True, notify_delivery=True, notify_incident=True)
    cl_med = get_or_create_client("logistics@medsupply-cf.com",
        name="MedSupply Central Florida", phone="+1 407 555 0202", codigo_cliente="CLI-MED",
        ciudad="Orlando", estado="FL", notify_batch_status=True, notify_stop_status=True,
        notify_delivery=True, notify_incident=True)
    cl_tch = get_or_create_client("dist@techcorp-fl.com",
        name="TechCorp Distribuciones", phone="+1 305 555 0303", codigo_cliente="CLI-TCH",
        ciudad="Orlando", estado="FL", notify_batch_status=True, notify_delivery=True)
    print("✓ 3 clientes listos")

    # ── Empresas de transporte (2, para poder probar pujas en paralelo) ──────
    tc1 = db.execute(select(TransportCompany).where(TransportCompany.name == "Rapid Courier Orlando LLC")).scalar_one_or_none()
    if not tc1:
        print("❌ Falta 'Rapid Courier Orlando LLC' — corré seed_fleet.py o el bootstrap original primero.")
        sys.exit(1)

    tc2 = db.execute(select(TransportCompany).where(TransportCompany.name == "Sunshine Logistics FL")).scalar_one_or_none()
    if not tc2:
        tc2 = TransportCompany(
            id=uid(), name="Sunshine Logistics FL", is_verified=False, is_active=True,
            service_modes=["mensajeria", "logistica"], city="Kissimmee", state="FL",
            avg_rating=4.2, on_time_pct=88.0, total_routes=6, completed_routes=5, rejected_routes=0,
            contact_name="Miguel Torres", contact_phone="+1 407 555 0198",
        )
        db.add(tc2); db.flush()
        sunshine_user = db.execute(select(User).where(User.email == "sunshine@muevo.app")).scalar_one_or_none()
        if not sunshine_user:
            sunshine_user = User(
                id=uid(), email="sunshine@muevo.app", full_name="Miguel Torres",
                avatar_initials="MT", role=UserRole.CORP_ADMIN, hashed_password=hash_password("muevo123"),
                is_active=True, is_verified=True,
            )
            db.add(sunshine_user); db.flush()
        db.add(TransportCompanyAdmin(id=uid(), transport_company_id=tc2.id, user_id=sunshine_user.id,
                                      is_primary=True, can_accept_routes=True))
        db.flush()
    tc1_user = db.execute(
        select(User).join(TransportCompanyAdmin, TransportCompanyAdmin.user_id == User.id)
        .where(TransportCompanyAdmin.transport_company_id == tc1.id)
    ).scalars().first()
    tc2_user = db.execute(select(User).where(User.email == "sunshine@muevo.app")).scalar_one_or_none()
    tc1_vehicles = db.execute(select(Vehicle).where(Vehicle.transport_company_id == tc1.id)).scalars().all()
    print(f"✓ Transportistas: {tc1.name} (verificado) / {tc2.name} (sin verificar)")

    # ── Helpers de creación (reusan la lógica real de main.py) ──────────────
    def make_route(title, stop_indices, svc, mode, sched_date, client_code, vehicle_type="furgoneta"):
        num = f"ORL-{sched_date.strftime('%Y%m%d')}-{uid().hex[:5].upper()}"
        r = RouteHeader(
            id=uid(), company_id=company.id, origin_warehouse_id=wh.id if wh else None,
            route_number=num, title=title, service_mode=mode,
            service_type_id=svc.id if svc else None, required_vehicle_type=vehicle_type,
            client_code=client_code, status=RouteStatus.DRAFT,
            scheduled_date=datetime.combine(sched_date, datetime.min.time()),
            scheduled_start=datetime.combine(sched_date, hhmm(8)),
            scheduled_end=datetime.combine(sched_date, hhmm(17)),
            gross_pay=0, muevo_commission_amt=0,
        )
        db.add(r); db.flush()
        for seq, idx in enumerate(stop_indices, 1):
            _create_route_detail(db, r.id, seq, stop_dict(idx, client_code),
                                  eta_scheduled=datetime.combine(sched_date, hhmm(min(8 + seq, 20))))
        db.flush(); db.refresh(r)
        _apply_formula_price(r, svc)
        return r

    def make_batch(notes, status, client, client_code, route_specs, sched_date, vehicle_type, service_type):
        """route_specs = lista de (title, stop_indices, svc, mode)"""
        routes = [make_route(title, idxs, svc, mode, sched_date, client_code, vehicle_type)
                  for (title, idxs, svc, mode) in route_specs]
        b = RouteBatch(
            id=uid(), company_id=company.id, batch_number=next_batch_number(company.id, db),
            client_id=client.id if client else None, client_code=client_code,
            vehicle_type=vehicle_type, service_type=service_type, status=status, notes=notes,
        )
        db.add(b); db.flush()
        for r in routes:
            db.add(RouteBatchItem(id=uid(), batch_id=b.id, route_header_id=r.id, status=BatchItemStatus.PENDING))
        db.flush()
        print(f"  ✓ Lote '{notes}' — {len(routes)} ruta(s) — {status.value}")
        return b, routes

    def approve_batch(b, routes):
        """Aprueba el lote y abre el marketplace de cada ruta — mismo efecto que change_batch_status."""
        b.status = BatchStatus.APPROVED
        for r in routes:
            _open_route_marketplace(r, carlos, db)
        db.flush()

    def place_bid(route, tc, tc_user, amount, note):
        offer = RoutePriceOffer(
            id=uid(), route_header_id=route.id, transport_company_id=tc.id,
            offered_by=OfferSource.TRANSPORT, offered_by_user_id=tc_user.id,
            amount=amount, note=note, status=OfferStatus.PENDING,
        )
        db.add(offer); db.flush()
        return offer

    def select_winner(route, winning_offer, other_offers):
        for o in other_offers:
            o.status = OfferStatus.REJECTED
        winning_offer.status = OfferStatus.SELECTED
        route.pending_offer_id = winning_offer.id
        route.negotiation_status = NegotiationStatus.PENDING_HOLDING_APPROVAL
        db.flush()

    def close_negotiation_as_accepted(route, winning_offer, tc):
        _close_negotiation(route, float(winning_offer.amount), db)
        winning_offer.status = OfferStatus.ACCEPTED
        route.transport_company_id = tc.id
        route.holding_approved_by = super_admin.id if super_admin else carlos.id
        route.holding_approved_at = datetime.utcnow()
        route.pending_offer_id = None
        route.status = RouteStatus.PUBLISHED
        db.flush()

    today, yesterday, last_week = date.today(), date.today() - timedelta(days=1), date.today() - timedelta(days=7)
    tomorrow, next_week = date.today() + timedelta(days=1), date.today() + timedelta(days=3)

    print("\nCreando lotes...")

    # ── 1. BORRADOR — sin aprobar, sin negociación ───────────────────────────
    b1, r1 = make_batch(
        "Distribución General — Sin Confirmar", BatchStatus.DRAFT, None, None,
        [("Distribución Mall Area", [16, 17, 22], st_log, ServiceMode.LOGISTICA),
         ("Almacenes Zona Norte", [25, 26, 27], st_log, ServiceMode.LOGISTICA),
         ("Logística Aeropuerto", [27, 13, 14], st_log, ServiceMode.LOGISTICA)],
        next_week + timedelta(days=2), "furgon", "logistica",
    )

    # ── 2. APROBADO — marketplace recién abierto, sin pujas todavía ──────────
    b2, r2 = make_batch(
        "Paquetería Tech — Mañana", BatchStatus.DRAFT, cl_tch, "CLI-TCH",
        [("Hardware Internacional Drive", [15, 16, 22, 13], st_pkg, ServiceMode.PAQUETERIA),
         ("Periféricos Theme Parks Area", [11, 12, 14], st_pkg, ServiceMode.PAQUETERIA),
         ("Servidores — Zona Industrial", [27, 26, 21], st_pkg, ServiceMode.PAQUETERIA)],
        tomorrow, "furgoneta", "paqueteria",
    )
    approve_batch(b2, r2)

    # ── 3. APROBADO — pujas activas de 2 transportistas en paralelo ─────────
    b3, r3 = make_batch(
        "Entregas Express — Multi Transportista", BatchStatus.DRAFT, cl_leg, "CLI-LEG",
        [("Documentos Legales — Zona Sur", [1, 5, 17, 24], st_msg, ServiceMode.MENSAJERIA),
         ("Notarías y Escribanías — Centro", [2, 3, 7, 18], st_msg, ServiceMode.MENSAJERIA)],
        next_week, "furgoneta", "mensajeria",
    )
    approve_batch(b3, r3)
    ruta_multi, ruta_simple = r3
    ask = float(ruta_multi.suggested_price)
    place_bid(ruta_multi, tc1, tc1_user, round(ask * 0.95, 2), "Podemos hacerlo un poco más barato")
    place_bid(ruta_multi, tc2, tc2_user, round(ask * 1.05, 2), "Pedimos un poco más por la zona de peaje")
    place_bid(ruta_simple, tc1, tc1_user, float(ruta_simple.suggested_price), "Aceptó el precio público")

    # ── 4. APROBADO — oferta seleccionada, esperando aprobación de holding ──
    b4, r4 = make_batch(
        "Suministros Médicos — Selección en curso", BatchStatus.DRAFT, cl_med, "CLI-MED",
        [("Suministros Hospitales — Zona Central", [9, 10, 8, 23], st_log, ServiceMode.LOGISTICA)],
        today, "furgon", "logistica",
    )
    approve_batch(b4, r4)
    ruta_holding = r4[0]
    offer_holding = place_bid(ruta_holding, tc1, tc1_user,
                               round(float(ruta_holding.suggested_price) * 0.97, 2), "Podemos cubrirla hoy mismo")
    select_winner(ruta_holding, offer_holding, [])

    # ── 5. APROBADO — ruta ya negociada y publicada, falta que la acepten ───
    b5, r5 = make_batch(
        "Reactivos Lab — Negociada", BatchStatus.DRAFT, cl_med, "CLI-MED",
        [("Reactivos Lab — Zona Este", [10, 24, 17], st_log, ServiceMode.LOGISTICA)],
        tomorrow, "furgon", "logistica",
    )
    approve_batch(b5, r5)
    ruta_negociada = r5[0]
    offer_negociada = place_bid(ruta_negociada, tc1, tc1_user,
                                 round(float(ruta_negociada.suggested_price) * 0.93, 2), "Nuestra mejor oferta")
    select_winner(ruta_negociada, offer_negociada, [])
    close_negotiation_as_accepted(ruta_negociada, offer_negociada, tc1)

    # ── 6. EN CURSO — despacho manual directo (fuera del marketplace) ───────
    b6, r6 = make_batch(
        "Logística Médica — Hoy", BatchStatus.APPROVED, cl_med, "CLI-MED",
        [("Equipos Clínicos — Zona Norte", [25, 27, 8], st_log, ServiceMode.LOGISTICA),
         ("Suministros Urgentes — Downtown", [9, 10, 23], st_log, ServiceMode.LOGISTICA)],
        today, "furgon", "logistica",
    )
    ruta_en_curso, ruta_publicada = r6
    ruta_en_curso.transport_company_id = tc1.id
    ruta_en_curso.status = RouteStatus.IN_PROGRESS
    ruta_en_curso.completed_stops = 1
    if tc1_vehicles:
        ruta_en_curso.vehicle_id = tc1_vehicles[0].id
    ruta_en_curso.details[0].status = StopStatus.COMPLETED
    ruta_publicada.transport_company_id = tc1.id
    ruta_publicada.status = RouteStatus.PUBLISHED

    # ── 7. COMPLETADO — histórico, para facturación/reportes ────────────────
    b7, r7 = make_batch(
        "Mensajería Legal — Semana Pasada", BatchStatus.COMPLETED, cl_leg, "CLI-LEG",
        [("Bufetes Downtown — Zona Norte", [0, 4, 6, 18, 19], st_msg, ServiceMode.MENSAJERIA),
         ("Documentos Corporativos — Orange Ave", [5, 6, 20, 1], st_msg, ServiceMode.MENSAJERIA),
         ("Expedientes Judiciales — Centro", [2, 3, 7, 0], st_msg, ServiceMode.MENSAJERIA)],
        last_week, "furgoneta", "mensajeria",
    )
    for r in r7:
        r.transport_company_id = tc1.id
        r.status = RouteStatus.COMPLETED
        r.completed_stops = r.total_stops
        if tc1_vehicles:
            r.vehicle_id = tc1_vehicles[0].id
        for d in r.details:
            d.status = StopStatus.COMPLETED

    # ── 8. CANCELADO — lote aprobado, anulado con motivo ─────────────────────
    b8, r8 = make_batch(
        "Ruta Descontinuada — Cliente Canceló", BatchStatus.DRAFT, cl_tch, "CLI-TCH",
        [("Entrega Descartada — Convention Center", [15, 16], st_pkg, ServiceMode.PAQUETERIA)],
        next_week, "sedan", "paqueteria",
    )
    approve_batch(b8, r8)
    b8.status = BatchStatus.CANCELLED
    b8.cancellation_reason = "El cliente canceló el pedido antes del despacho."
    b8.cancelled_at = datetime.utcnow()

    db.commit()

    print("\n" + "─" * 70)
    print("✅ Seed completado — 8 lotes, 16 rutas, precios de fórmula, marketplace real:")
    print(f"   1. {b1.batch_number} '{b1.notes}' — BORRADOR, sin negociación")
    print(f"   2. {b2.batch_number} '{b2.notes}' — APROBADO, marketplace abierto, sin pujas")
    print(f"   3. {b3.batch_number} '{b3.notes}' — APROBADO, {ruta_multi.route_number} con 2 pujas activas (TC1 y TC2)")
    print(f"   4. {b4.batch_number} '{b4.notes}' — APROBADO, {ruta_holding.route_number} EN HOLDING (requiere super_admin)")
    print(f"   5. {b5.batch_number} '{b5.notes}' — {ruta_negociada.route_number} NEGOCIADA, {tc1.name} debe aceptarla")
    print(f"   6. {b6.batch_number} '{b6.notes}' — EN CURSO (despacho manual, {ruta_en_curso.route_number})")
    print(f"   7. {b7.batch_number} '{b7.notes}' — COMPLETADO (histórico)")
    print(f"   8. {b8.batch_number} '{b8.notes}' — CANCELADO con motivo")
    print()
    print("   Logins:")
    print("     Warehouse (holding-wide):  carlos@muevo.app / muevo123")
    print("     Warehouse (holding-wide):  super.admin@muevo.app / muevo123")
    print("     Transportista verificado:  transport@muevo.app / muevo123 (Rapid Courier Orlando LLC)")
    print("     Transportista sin verif.:  sunshine@muevo.app / muevo123 (Sunshine Logistics FL)")
    print("─" * 70)
