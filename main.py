"""
main.py — Muevo Backend (FastAPI)
==================================
Backend conectado a PostgreSQL con SQLAlchemy.

Levantar con:
    uvicorn main:app --reload --port 8000

Endpoints:
  GET  /health
  POST /api/v1/auth/token
  POST /api/v1/auth/register
  POST /api/v1/rides/estimate
  POST /api/v1/rides/request
  GET  /api/v1/rides/{id}/status
  POST /api/v1/rides/{id}/cancel
  GET  /api/v1/drivers/nearby
  GET  /api/v1/drivers/me/earnings
  PATCH /api/v1/drivers/me/location
  PATCH /api/v1/drivers/me/status

  -- B2B Drive --
  GET  /api/v1/routes
  GET  /api/v1/routes/{id}
  POST /api/v1/routes/{id}/accept
  POST /api/v1/routes/{id}/start
  POST /api/v1/routes/{id}/stops/{stop_id}/complete
  GET  /api/v1/drivers/me/metrics
"""

import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Optional, List

import jwt
from fastapi import Depends, FastAPI, HTTPException, status, WebSocket, WebSocketDisconnect, Header, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import bcrypt as _bcrypt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, text, select, update, func, delete as sa_delete
from sqlalchemy.orm import Session, sessionmaker

from dispatcher import Stop as DispatchStop, plan_stops, planned_route_to_dict
from models import (
    Base, User, Driver, Vehicle, Route, Stop, Delivery,
    Earning, DriverMetric, Company,
    UserRole, RouteStatus, StopStatus, PodType,
    # Modelo multi-holding / multi-warehouse (vigente para /api/v1/routes)
    Holding, Warehouse, TransportCompany,
    RouteHeader, RouteDetail, ShipmentItem, CompanyAdmin, TransportCompanyAdmin,
    # Batches e incidencias
    RouteBatch, RouteBatchItem, IncidentType, Incident,
    BatchStatus, BatchItemStatus, IncidentStatus, IncidentSeverity, ReporterRole,
    Contract,
    # Holding user management
    HoldingUser, HoldingUserCompany,
    # Service types
    ServiceType,
    # Vehicle types
    VehicleTypeConfig,
    # Admin — profiles & permissions
    Module, Action, Permission, Profile, ProfilePermission, HoldingUserProfile,
    # Batch clients
    BatchClient,
    # Warehouse inventory
    WarehouseInventoryItem, InventoryItemStatus,
    # Recepción de mercancía (inbound)
    Reception, ReceptionItem, ReceptionSourceType, ReceptionStatus, ReceptionItemStatus,
    # Catálogo de mensajes
    MessageTemplate,
    # Negociación de precios
    RoutePriceOffer, NegotiationStatus, OfferSource, OfferStatus,
    # Company & Warehouse management
    Warehouse,
    # Auditoría
    AuditLog, AuditAction, current_audit_user_id,
)

# ─── Config ───────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:Semeolvido.01@localhost:5432/muevodb"
)

SECRET_KEY        = os.getenv("SECRET_KEY", "muevo-secret-key-cambiar-en-produccion")
ALGORITHM         = "HS256"
TOKEN_EXPIRE_HOURS = 24

# Límites de carga de CSV — default global, sobreescribible por compañía
# (Company.max_csv_rows / max_csv_file_size_mb; NULL = usar este default).
DEFAULT_MAX_CSV_ROWS         = 2000
DEFAULT_MAX_CSV_FILE_SIZE_MB = 5

# ─── Database ─────────────────────────────────────────────────────────────────

engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ─── Security ─────────────────────────────────────────────────────────────────

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password[:72].encode(), _bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain[:72].encode(), hashed.encode())
    except Exception:
        return False

def create_token(user_id: str, email: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": str(user_id), "email": email, "exp": expire},
        SECRET_KEY, algorithm=ALGORITHM,
    )

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token inválido")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido")

    user = db.get(User, uuid.UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return user

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Muevo API",
    description="Backend de movilidad y logística B2B/B2C",
    version="1.0.0",
)

# ─── WebSocket — tracking en tiempo real ──────────────────────────────────────
# Cada company tiene su propio "canal": cuando un conductor con una ruta
# activa para esa company actualiza su GPS, se transmite a todos los
# clientes de warehouse.html conectados a ese canal.

class TrackingConnectionManager:
    def __init__(self):
        # company_id (str) -> lista de WebSocket conectados
        self.active: dict[str, list[WebSocket]] = {}

    async def connect(self, company_id: str, ws: WebSocket):
        await ws.accept()
        self.active.setdefault(company_id, []).append(ws)

    def disconnect(self, company_id: str, ws: WebSocket):
        if company_id in self.active and ws in self.active[company_id]:
            self.active[company_id].remove(ws)
            if not self.active[company_id]:
                del self.active[company_id]

    async def broadcast(self, company_id: str, message: dict):
        for ws in list(self.active.get(company_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(company_id, ws)

tracking_manager = TrackingConnectionManager()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8081",
        "http://localhost:8082",
        "http://localhost:3000",
        "http://127.0.0.1:8081",
        "http://127.0.0.1:8082",
        "null",
    ],
    allow_origin_regex=r"https?://.*\.ngrok(-free)?\.app",  # permite cualquier túnel ngrok
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def audit_user_context_middleware(request: Request, call_next):
    """
    Decodifica el JWT del header Authorization (si viene) y lo deja en un
    ContextVar para que el listener before_flush de models.py pueda anotar
    quién hizo cada cambio de auditoría, sin que cada endpoint tenga que
    pasarlo explícitamente. Best-effort: un token ausente o inválido no
    bloquea la request, solo deja el autor de la auditoría en None (el
    401 real, si corresponde, lo sigue dando get_current_user).
    """
    token_value = None
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        raw_token = auth_header[7:]
        try:
            payload = jwt.decode(raw_token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
            if user_id:
                token_value = uuid.UUID(user_id)
        except Exception:
            token_value = None

    reset_token = current_audit_user_id.set(token_value)
    try:
        return await call_next(request)
    finally:
        current_audit_user_id.reset(reset_token)


# ─── Schemas Pydantic ─────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    full_name: str
    email:     str
    password:  str
    phone:     Optional[str] = None
    role:      str = "driver"

class EstimateRequest(BaseModel):
    service_type:      str
    origin_lat:        float
    origin_lng:        float
    dest_lat:          float
    dest_lng:          float
    extras:            list[str] = []
    demand_multiplier: float = 1.0
    preferred_language:str = "any"
    vehicle_type:      str = "sedan"
    vehicle_mpg:       float = 28.0
    city_state:        str = "New York, NY"

class RideRequest(EstimateRequest):
    payment_method: str = "card"

class LocationUpdate(BaseModel):
    lat: float
    lng: float

class StatusUpdate(BaseModel):
    is_online: bool

class DeliveryConfirm(BaseModel):
    pod_type:       str
    signature_url:  Optional[str] = None
    photo_url:      Optional[str] = None
    scanned_code:   Optional[str] = None
    signer_name:    Optional[str] = None
    driver_notes:   Optional[str] = None
    delivery_lat:   Optional[float] = None
    delivery_lng:   Optional[float] = None

# ─── Helpers ──────────────────────────────────────────────────────────────────

def haversine_miles(lat1, lng1, lat2, lng2) -> float:
    from math import radians, cos, sin, asin, sqrt
    R = 3958.8
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlng/2)**2
    return 2 * R * asin(sqrt(a))

def format_user(user: User) -> dict:
    return {
        "id":              str(user.id),
        "name":            user.full_name,
        "email":           user.email,
        "avatar_initials": user.avatar_initials or user.full_name[:2].upper(),
        "wallet_balance":  0.0,
        "role":            user.role.value if hasattr(user.role, 'value') else user.role,
    }

# ─── Static — Warehouse Portal (sirve warehouse.html directamente) ───────────
# Permite acceder al portal warehouse desde la misma URL del backend (útil al
# exponer todo con un solo túnel de ngrok). El archivo debe estar en la misma
# carpeta que main.py.

@app.get("/login.html", include_in_schema=False)
async def serve_login_portal():
    return FileResponse("login.html", media_type="text/html")


@app.get("/warehouse.html", include_in_schema=False)
async def serve_warehouse_portal():
    return FileResponse("warehouse.html", media_type="text/html")


@app.get("/transport.html", include_in_schema=False)
async def serve_transport_portal():
    return FileResponse("transport.html", media_type="text/html")


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "error"
    return {
        "status":   "ok",
        "version":  "1.0.0",
        "app":      "Muevo",
        "database": db_status,
    }

# ─── Auth ─────────────────────────────────────────────────────────────────────

def resolve_user_portal(user: User, db: Session) -> dict:
    """
    Determina a qué portal pertenece un usuario autenticado.
    Prioridad: holding_user > transport_admin > driver.

    Para holding users devuelve la lista de compañías accesibles.
    Si solo tiene una compañía, redirige directo. Si tiene más de una,
    login.html mostrará el selector de compañía antes de entrar al portal.
    """
    # ── Holding user (warehouse portal) ──────────────────────────────────────
    holding_user = db.execute(
        select(HoldingUser).where(
            HoldingUser.user_id == user.id,
            HoldingUser.is_active == True,
        )
    ).scalar_one_or_none()

    if holding_user:
        # Obtener compañías accesibles con sus permisos
        accesses = db.execute(
            select(HoldingUserCompany)
            .where(HoldingUserCompany.holding_user_id == holding_user.id)
            .where(HoldingUserCompany.can_view == True)
        ).scalars().all()

        companies = []
        for acc in accesses:
            company = db.get(Company, acc.company_id)
            if company:
                companies.append({
                    "id":          str(company.id),
                    "name":        company.name,
                    "can_view":    acc.can_view,
                    "can_operate": acc.can_operate,
                    "can_invoice": acc.can_invoice,
                    "is_admin":    acc.is_admin,
                })

        # Super-admin ve todas las compañías del holding
        if holding_user.is_super_admin and not companies:
            all_companies = db.execute(
                select(Company).where(Company.holding_id == holding_user.holding_id)
            ).scalars().all()
            companies = [{
                "id": str(c.id), "name": c.name,
                "can_view": True, "can_operate": True,
                "can_invoice": True, "is_admin": True,
            } for c in all_companies]

        return {
            "portal":          "warehouse",
            "redirect":        "/warehouse.html",
            "holding_user_id": str(holding_user.id),
            "is_super_admin":  holding_user.is_super_admin,
            "companies":       companies,
            # Si tiene exactamente 1 compañía, el cliente puede entrar directo
            "auto_company":    companies[0] if len(companies) == 1 else None,
        }

    # ── Legacy company_admin (backward compat) ────────────────────────────────
    company_admin = db.execute(
        select(CompanyAdmin).where(CompanyAdmin.user_id == user.id)
    ).scalar_one_or_none()
    if company_admin:
        company = db.get(Company, company_admin.company_id)
        return {
            "portal":    "warehouse",
            "redirect":  "/warehouse.html",
            "companies": [{"id": str(company_admin.company_id), "name": company.name if company else "",
                           "can_view": True, "can_operate": True, "can_invoice": True, "is_admin": True}],
            "auto_company": {"id": str(company_admin.company_id), "name": company.name if company else ""},
        }

    # ── Transport admin ───────────────────────────────────────────────────────
    tc_admin = db.execute(
        select(TransportCompanyAdmin).where(TransportCompanyAdmin.user_id == user.id)
    ).scalar_one_or_none()
    if tc_admin:
        return {"portal": "transport", "redirect": "/transport.html"}

    # ── Driver ────────────────────────────────────────────────────────────────
    driver = db.execute(
        select(Driver).where(Driver.user_id == user.id)
    ).scalar_one_or_none()
    if driver:
        return {"portal": "driver", "redirect": None}

    return {"portal": "unknown", "redirect": None}


@app.post("/api/v1/auth/token")
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db:   Session = Depends(get_db),
):
    user = db.execute(
        select(User).where(User.email == form.username)
    ).scalar_one_or_none()

    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="Cuenta desactivada")

    # Actualizar último login
    user.last_login_at = datetime.utcnow()
    db.commit()

    token = create_token(str(user.id), user.email)
    return {
        "access_token": token,
        "token_type":   "bearer",
        "user":         format_user(user),
        **resolve_user_portal(user, db),
    }


@app.post("/api/v1/auth/register", status_code=201)
async def register(
    data: RegisterRequest,
    db:   Session = Depends(get_db),
):
    existing = db.execute(
        select(User).where(User.email == data.email)
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(status_code=400, detail="El email ya está registrado")

    initials = "".join(w[0].upper() for w in data.full_name.split()[:2])
    role     = UserRole(data.role) if data.role in [r.value for r in UserRole] else UserRole.DRIVER

    user = User(
        id              = uuid.uuid4(),
        email           = data.email,
        full_name       = data.full_name,
        phone           = data.phone,
        avatar_initials = initials,
        role            = role,
        hashed_password = hash_password(data.password),
        is_active       = True,
        is_verified     = False,
    )
    db.add(user)

    # Si es conductor, crear perfil de driver automáticamente
    if role == UserRole.DRIVER:
        driver = Driver(
            id           = uuid.uuid4(),
            user_id      = user.id,
            is_online    = False,
            is_approved  = False,
            service_modes= ["mensajeria"],
        )
        db.add(driver)

    db.commit()
    db.refresh(user)

    token = create_token(str(user.id), user.email)
    return {
        "access_token": token,
        "token_type":   "bearer",
        "user":         format_user(user),
    }

# ─── Rides — estimador ────────────────────────────────────────────────────────

@app.post("/api/v1/rides/estimate")
async def estimate_ride(data: EstimateRequest):
    """Calcula el precio real usando pricing.py."""
    try:
        from pricing import (
            calculate_trip_pricing, RidePricingRequest,
            DriverVehicleInfo, ServiceType, Extra,
        )

        distance_miles   = haversine_miles(
            data.origin_lat, data.origin_lng,
            data.dest_lat,   data.dest_lng,
        )
        duration_minutes = (distance_miles / 15.0) * 60

        service_map = {
            "ride_standard": ServiceType.RIDE_STANDARD,
            "ride":          ServiceType.RIDE_STANDARD,
            "ride_xl":       ServiceType.RIDE_XL,
            "ride_premium":  ServiceType.RIDE_PREMIUM,
            "ride_eco":      ServiceType.RIDE_ECO,
            "ride_airport":  ServiceType.RIDE_AIRPORT,
        }
        service_type = service_map.get(data.service_type, ServiceType.RIDE_STANDARD)

        extras_map = {
            "luggage": Extra.EXTRA_LUGGAGE,
            "child":   Extra.CHILD_SEAT,
            "silent":  Extra.SILENT_RIDE,
            "lock":    Extra.PRICE_LOCK,
            "pet":     Extra.EXTRA_LUGGAGE,
            "senior":  Extra.SENIOR_ASSIST,
        }
        parsed_extras = [extras_map[e] for e in data.extras if e in extras_map]

        pricing_request = RidePricingRequest(
            service_type     = service_type,
            distance_miles   = max(distance_miles, 0.5),
            duration_minutes = max(duration_minutes, 2.0),
            deadhead_miles   = 0.8,
            driver_vehicle   = DriverVehicleInfo(
                vehicle_type = data.vehicle_type,
                mpg          = data.vehicle_mpg,
                odometer     = 0,
            ),
            extras              = parsed_extras,
            demand_multiplier   = data.demand_multiplier,
            platform_commission = 0.10,
            city_state          = data.city_state,
        )

        result = await calculate_trip_pricing(pricing_request)

        eta_map = {
            "ride": 4, "ride_standard": 4, "ride_xl": 6,
            "ride_premium": 8, "ride_eco": 5, "ride_airport": 5,
        }

        return {
            "gross_fare":        float(result.fare.total_fare),
            "driver_net":        float(result.driver_net),
            "platform_fee":      float(result.costs.platform_fee),
            "fuel_cost":         float(result.costs.fuel_cost),
            "wear_cost":         float(result.costs.wear_cost),
            "margin_pct":        float(result.margin_pct),
            "net_per_mile":      float(result.net_per_mile),
            "profitability":     result.profitability.value,
            "ai_recommendation": result.ai_recommendation,
            "distance_miles":    round(distance_miles, 2),
            "duration_minutes":  round(duration_minutes, 1),
            "eta_minutes":       eta_map.get(data.service_type, 5),
            "gas_price_used":    float(result.gas_price_used),
            "service_type":      data.service_type,
        }

    except Exception as e:
        # Fallback si pricing.py no está disponible
        distance_miles = haversine_miles(
            data.origin_lat, data.origin_lng,
            data.dest_lat,   data.dest_lng,
        )
        base_fares = {
            "ride": 9.50, "ride_standard": 9.50, "ride_xl": 16.00,
            "ride_premium": 28.00, "ride_eco": 8.00, "ride_airport": 32.00,
        }
        gross = base_fares.get(data.service_type, 9.50)
        return {
            "gross_fare":       gross,
            "driver_net":       gross * 0.90,
            "platform_fee":     gross * 0.10,
            "fuel_cost":        distance_miles * 0.18,
            "wear_cost":        distance_miles * 0.10,
            "margin_pct":       80.0,
            "net_per_mile":     2.50,
            "profitability":    "good",
            "ai_recommendation":"Precio estimado (fallback)",
            "distance_miles":   round(distance_miles, 2),
            "duration_minutes": round((distance_miles / 15.0) * 60, 1),
            "eta_minutes":      5,
            "gas_price_used":   3.50,
            "service_type":     data.service_type,
        }


@app.post("/api/v1/rides/request", status_code=201)
async def request_ride(
    data:         RideRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Crea un viaje en la base de datos."""
    estimate = await estimate_ride(data)

    ride_id = str(uuid.uuid4())

    return {
        "id":                  ride_id,
        "passenger_id":        str(current_user.id),
        "service_type":        data.service_type,
        "status":              "driver_assigned",
        "origin":              {"latitude": data.origin_lat, "longitude": data.origin_lng},
        "destination":         {"latitude": data.dest_lat,   "longitude": data.dest_lng},
        "origin_address":      "Times Square, New York",
        "destination_address": "Destino seleccionado",
        "extras":              data.extras,
        "preferred_language":  data.preferred_language,
        "distance_miles":      estimate["distance_miles"],
        "duration_minutes":    estimate["duration_minutes"],
        "pricing":             {
            "gross_fare":   estimate["gross_fare"],
            "driver_net":   estimate["driver_net"],
            "platform_fee": estimate["platform_fee"],
            "fuel_cost":    estimate["fuel_cost"],
            "wear_cost":    estimate["wear_cost"],
            "margin_pct":   estimate["margin_pct"],
            "eta_minutes":  estimate["eta_minutes"],
        },
        "driver": {
            "id":               "driver-001",
            "name":             "Carlos Mendez",
            "rating":           4.93,
            "total_trips":      2841,
            "vehicle_make":     "Toyota",
            "vehicle_model":    "Camry",
            "vehicle_year":     2022,
            "vehicle_color":    "Gris",
            "plate":            "ABC-123",
            "is_bilingual":     True,
            "current_location": {
                "latitude":  data.origin_lat + 0.01,
                "longitude": data.origin_lng + 0.01,
            },
            "avatar_initials": "CM",
        },
        "created_at": datetime.utcnow().isoformat(),
    }


@app.get("/api/v1/rides/{ride_id}/status")
async def get_ride_status(
    ride_id:      str,
    current_user: User = Depends(get_current_user),
):
    return {"id": ride_id, "status": "driver_arriving"}


@app.post("/api/v1/rides/{ride_id}/cancel")
async def cancel_ride(
    ride_id:      str,
    current_user: User = Depends(get_current_user),
):
    return {"id": ride_id, "status": "cancelled"}

# ─── Drivers ──────────────────────────────────────────────────────────────────

@app.get("/api/v1/drivers/nearby")
async def get_nearby_drivers(
    lat:          float = 40.758,
    lng:          float = -73.9855,
    service_type: str   = "ride",
    current_user: User  = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Devuelve conductores cercanos online."""
    drivers = db.execute(
        select(Driver, User)
        .join(User, Driver.user_id == User.id)
        .where(Driver.is_online == True)
        .where(Driver.is_approved == True)
        .limit(10)
    ).all()

    result = []
    for driver, user in drivers:
        if driver.current_lat and driver.current_lng:
            dist = haversine_miles(lat, lng, driver.current_lat, driver.current_lng)
        else:
            dist = 0.5
        result.append({
            "id":             str(driver.id),
            "name":           user.full_name,
            "rating":         float(driver.avg_rating),
            "distance_miles": round(dist, 2),
            "eta_minutes":    max(2, int(dist * 4)),
            "is_bilingual":   user.preferred_language != "en",
        })

    return result


@app.patch("/api/v1/drivers/me/location")
async def update_location(
    data:         LocationUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    driver = db.execute(
        select(Driver).where(Driver.user_id == current_user.id)
    ).scalar_one_or_none()

    if not driver:
        raise HTTPException(status_code=404, detail="Perfil de conductor no encontrado")

    driver.current_lat      = data.lat
    driver.current_lng      = data.lng
    driver.last_location_at = datetime.utcnow()
    db.commit()

    # Transmitir la posición a las companies con una ruta activa de este conductor
    vehicle = db.execute(
        select(Vehicle).where(Vehicle.driver_id == driver.id, Vehicle.is_active == True)
    ).scalar_one_or_none()
    if vehicle and vehicle.transport_company_id:
        active_routes = db.execute(
            select(RouteHeader)
            .where(RouteHeader.vehicle_id == vehicle.id)
            .where(RouteHeader.status == RouteStatus.IN_PROGRESS)
        ).scalars().all()
        for route in active_routes:
            await tracking_manager.broadcast(str(route.company_id), {
                "type":      "driver_location",
                "route_id":  str(route.id),
                "route_number": route.route_number,
                "lat":       data.lat,
                "lng":       data.lng,
                "timestamp": driver.last_location_at.isoformat(),
            })

        # Transmitir también al canal de flota de la empresa de transporte
        # (independiente de si hay ruta activa — el dueño de la flota
        # quiere ver sus vehículos en el mapa en todo momento).
        active_route = active_routes[0] if active_routes else None
        await tracking_manager.broadcast(f"fleet-{vehicle.transport_company_id}", {
            "type":         "vehicle_location",
            "vehicle_id":   str(vehicle.id),
            "plate":        vehicle.plate,
            "route_id":     str(active_route.id) if active_route else None,
            "route_number": active_route.route_number if active_route else None,
            "lat":          data.lat,
            "lng":          data.lng,
            "timestamp":    driver.last_location_at.isoformat(),
        })

    return {"status": "updated", "lat": data.lat, "lng": data.lng}


@app.websocket("/ws/tracking/{company_id}")
async def tracking_websocket(websocket: WebSocket, company_id: str):
    """
    El portal warehouse se conecta aquí para recibir actualizaciones de
    posición en tiempo real de todos los conductores con rutas activas
    para su company. No requiere JWT en el handshake (limitación de
    WebSockets nativos del navegador) — usar solo en desarrollo/LAN;
    para producción, pasar un token corto como query param y validarlo.
    """
    await tracking_manager.connect(company_id, websocket)
    try:
        while True:
            # Mantenemos la conexión viva; no esperamos mensajes del cliente
            await websocket.receive_text()
    except WebSocketDisconnect:
        tracking_manager.disconnect(company_id, websocket)



@app.patch("/api/v1/drivers/me/status")
async def update_driver_status(
    data:         StatusUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    driver = db.execute(
        select(Driver).where(Driver.user_id == current_user.id)
    ).scalar_one_or_none()

    if not driver:
        raise HTTPException(status_code=404, detail="Perfil de conductor no encontrado")

    driver.is_online = data.is_online
    db.commit()

    return {"is_online": data.is_online}


@app.get("/api/v1/drivers/me/earnings")
async def get_earnings(
    period:       str = "today",
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    driver = db.execute(
        select(Driver).where(Driver.user_id == current_user.id)
    ).scalar_one_or_none()

    if not driver:
        return {"gross": 0, "net": 0, "trips": 0, "rating": 5.0}

    return {
        "gross":    float(driver.total_earnings_net) * 1.12,
        "net":      float(driver.total_earnings_net),
        "trips":    driver.completed_routes,
        "rating":   float(driver.avg_rating),
        "km_driven":float(driver.total_km_driven),
    }

# ─── B2B Routes (modelo Holding -> Company -> Warehouse -> RouteHeader) ───────

@app.get("/api/v1/routes")
async def list_routes(
    status:       Optional[str] = None,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Lista las rutas asignadas a la empresa de transporte del conductor autenticado."""
    driver = db.execute(
        select(Driver).where(Driver.user_id == current_user.id)
    ).scalar_one_or_none()

    if not driver:
        raise HTTPException(status_code=404, detail="Perfil de conductor no encontrado")

    # El conductor pertenece a una empresa de transporte vía su vehículo asignado
    vehicle = db.execute(
        select(Vehicle).where(Vehicle.driver_id == driver.id, Vehicle.is_active == True)
    ).scalar_one_or_none()
    transport_company_id = vehicle.transport_company_id if vehicle else None

    query = select(RouteHeader)
    if transport_company_id:
        query = query.where(RouteHeader.transport_company_id == transport_company_id)
    else:
        # Fallback: sin empresa de transporte asignada, no ve rutas ajenas
        query = query.where(RouteHeader.dispatched_by == current_user.id)

    if status:
        query = query.where(RouteHeader.status == RouteStatus(status))

    headers = db.execute(query.order_by(RouteHeader.scheduled_start)).scalars().all()

    return [serialize_route_header(rh) for rh in headers]


@app.get("/api/v1/routes/{route_id}")
async def get_route(
    route_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    header = db.get(RouteHeader, uuid.UUID(route_id))
    if not header:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    return serialize_route_header(header)


@app.post("/api/v1/routes/{route_id}/accept")
async def accept_route(
    route_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    header = db.get(RouteHeader, uuid.UUID(route_id))
    if not header:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    if header.status != RouteStatus.PUBLISHED:
        raise HTTPException(status_code=400, detail="La ruta no está disponible")

    driver = db.execute(
        select(Driver).where(Driver.user_id == current_user.id)
    ).scalar_one_or_none()
    vehicle = db.execute(
        select(Vehicle).where(Vehicle.driver_id == driver.id, Vehicle.is_active == True)
    ).scalar_one_or_none()

    header.vehicle_id           = vehicle.id if vehicle else None
    header.transport_company_id = vehicle.transport_company_id if vehicle else header.transport_company_id
    # Solo pasar a ASSIGNED si efectivamente hay una empresa de transporte asociada
    header.status = RouteStatus.ASSIGNED if header.transport_company_id else RouteStatus.PUBLISHED
    db.commit()

    return {"status": "assigned", "route_id": route_id}


@app.post("/api/v1/routes/{route_id}/start")
async def start_route(
    route_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    header = db.get(RouteHeader, uuid.UUID(route_id))
    if not header:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")

    header.status       = RouteStatus.IN_PROGRESS
    header.actual_start  = datetime.utcnow()
    db.commit()

    return {"status": "in_progress", "started_at": header.actual_start.isoformat()}


@app.post("/api/v1/routes/{route_id}/stops/{stop_id}/complete")
async def complete_stop(
    route_id:     str,
    stop_id:      str,
    data:         DeliveryConfirm,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Confirma la entrega en una parada (route_detail) con prueba de delivery."""
    detail = db.get(RouteDetail, uuid.UUID(stop_id))
    if not detail:
        raise HTTPException(status_code=404, detail="Parada no encontrada")

    driver = db.execute(
        select(Driver).where(Driver.user_id == current_user.id)
    ).scalar_one_or_none()

    # Actualizar estado de la parada
    detail.status       = StopStatus.COMPLETED
    detail.completed_at = datetime.utcnow()

    # Crear registro de entrega (PoD) vinculado al modelo vigente (RouteDetail)
    delivery = Delivery(
        id              = uuid.uuid4(),
        route_detail_id = detail.id,
        driver_id       = driver.id,
        pod_type     = PodType(data.pod_type) if data.pod_type in [p.value for p in PodType] else PodType.SIGNATURE,
        signature_url= data.signature_url,
        photo_url    = data.photo_url,
        scanned_code = data.scanned_code,
        signer_name  = data.signer_name,
        driver_notes = data.driver_notes,
        delivery_lat = data.delivery_lat,
        delivery_lng = data.delivery_lng,
        delivered_at = datetime.utcnow(),
        on_time      = True,
    )
    db.add(delivery)
    db.flush()  # asegura que el trigger de PostgreSQL recalcule totales antes del commit

    # El encabezado se mantiene sincronizado vía triggers, pero refrescamos
    # el estado de la ruta si ya no quedan paradas pendientes
    header = db.get(RouteHeader, uuid.UUID(route_id))
    db.refresh(header) if header else None

    route_completed = False
    if header:
        pending = db.execute(
            select(RouteDetail)
            .where(RouteDetail.route_header_id == header.id)
            .where(RouteDetail.status != StopStatus.COMPLETED)
        ).first()
        if pending is None:
            header.status     = RouteStatus.COMPLETED
            header.actual_end = datetime.utcnow()
            route_completed   = True

    db.commit()

    # Notificar al cliente del lote sobre entrega confirmada
    if detail and header:
        # Buscar el lote al que pertenece esta ruta
        batch_item = db.execute(
            select(RouteBatchItem).where(RouteBatchItem.route_header_id == header.id)
        ).scalar_one_or_none()
        if batch_item:
            await notify_batch_client(
                batch_id=batch_item.batch_id,
                event="delivery",
                subject=f"📦 Entrega confirmada — {detail.company_name or stop_id[:8]}",
                body_lines=[
                    f"Se ha confirmado una entrega en tu lote.",
                    f"<strong>Destinatario:</strong> {detail.company_name or 'N/D'}",
                    f"<strong>Dirección:</strong> {detail.address_line1 or 'N/D'}{', ' + detail.city if detail.city else ''}",
                    f"<strong>Tipo de PoD:</strong> {data.pod_type}",
                    f"<strong>Hora de entrega:</strong> {delivery.delivered_at.strftime('%d/%m/%Y %H:%M')} UTC",
                    f"<strong>Ruta:</strong> {header.route_number}",
                ],
                db=db,
            )

            # También notificar cambio de estado de parada si está habilitado
            await notify_batch_client(
                batch_id=batch_item.batch_id,
                event="stop_status",
                subject=f"🔄 Parada completada — {header.route_number}",
                body_lines=[
                    f"Una parada de tu ruta ha sido completada.",
                    f"<strong>Destinatario:</strong> {detail.company_name or 'N/D'}",
                    f"<strong>Ruta:</strong> {header.route_number}",
                    f"<strong>Progreso:</strong> {header.completed_stops}/{header.total_stops} paradas",
                ],
                db=db,
            )

    return {
        "stop_id":          stop_id,
        "status":           "completed",
        "delivered_at":     delivery.delivered_at.isoformat(),
        "route_completed":  route_completed,
    }


@app.get("/api/v1/drivers/me/metrics")
async def get_driver_metrics(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    driver = db.execute(
        select(Driver).where(Driver.user_id == current_user.id)
    ).scalar_one_or_none()

    if not driver:
        raise HTTPException(status_code=404, detail="Perfil de conductor no encontrado")

    metrics = db.execute(
        select(DriverMetric)
        .where(DriverMetric.driver_id == driver.id)
        .order_by(DriverMetric.metric_date.desc())
        .limit(7)
    ).scalars().all()

    return {
        "driver_id":        str(driver.id),
        "total_routes":     driver.total_routes,
        "completed_routes": driver.completed_routes,
        "avg_rating":       float(driver.avg_rating),
        "total_earnings":   float(driver.total_earnings_net),
        "total_km":         float(driver.total_km_driven),
        "daily_metrics":    [serialize_metric(m) for m in metrics],
    }

# ─── Vehicle Types (catálogo) ─────────────────────────────────────────────────

class VehicleTypeConfigCreate(BaseModel):
    code:            str
    name:            str
    description:     Optional[str]   = None
    peso_max_lbs:    float           = 0.0
    volumen_max_ft3: float           = 0.0
    habilitado:      bool            = True
    maneja_unidades: bool            = False
    unidades_max:    Optional[int]   = None


class VehicleTypeConfigUpdate(BaseModel):
    name:            Optional[str]   = None
    description:     Optional[str]   = None
    peso_max_lbs:    Optional[float] = None
    volumen_max_ft3: Optional[float] = None
    habilitado:      Optional[bool]  = None
    maneja_unidades: Optional[bool]  = None
    unidades_max:    Optional[int]   = None


def serialize_vehicle_type(vt: VehicleTypeConfig) -> dict:
    return {
        "id":              str(vt.id),
        "code":            vt.code,
        "name":            vt.name,
        "description":     vt.description,
        "peso_max_lbs":    float(vt.peso_max_lbs),
        "volumen_max_ft3": float(vt.volumen_max_ft3),
        "habilitado":      vt.habilitado,
        "maneja_unidades": vt.maneja_unidades,
        "unidades_max":    vt.unidades_max,
        "created_at":      vt.created_at.isoformat() if vt.created_at else None,
        "updated_at":      vt.updated_at.isoformat() if vt.updated_at else None,
    }


@app.get("/api/v1/vehicle-types")
async def list_vehicle_types(
    habilitado:   Optional[bool] = None,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Lista todos los tipos de vehículo, opcionalmente filtrando por habilitado."""
    query = select(VehicleTypeConfig).order_by(VehicleTypeConfig.name)
    if habilitado is not None:
        query = query.where(VehicleTypeConfig.habilitado == habilitado)
    types = db.execute(query).scalars().all()
    return [serialize_vehicle_type(vt) for vt in types]


@app.get("/api/v1/vehicle-types/{vehicle_type_id}")
async def get_vehicle_type(
    vehicle_type_id: str,
    current_user:    User    = Depends(get_current_user),
    db:              Session = Depends(get_db),
):
    vt = db.get(VehicleTypeConfig, uuid.UUID(vehicle_type_id))
    if not vt:
        raise HTTPException(status_code=404, detail="Tipo de vehículo no encontrado")
    return serialize_vehicle_type(vt)


@app.post("/api/v1/vehicle-types", status_code=201)
async def create_vehicle_type(
    data:         VehicleTypeConfigCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    existing = db.execute(
        select(VehicleTypeConfig).where(VehicleTypeConfig.code == data.code)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"Ya existe un tipo con código '{data.code}'")

    vt = VehicleTypeConfig(
        id=uuid.uuid4(), code=data.code, name=data.name,
        description=data.description,
        peso_max_lbs=data.peso_max_lbs, volumen_max_ft3=data.volumen_max_ft3,
        habilitado=data.habilitado,
        maneja_unidades=data.maneja_unidades, unidades_max=data.unidades_max,
    )
    db.add(vt)
    db.commit()
    return serialize_vehicle_type(vt)


@app.patch("/api/v1/vehicle-types/{vehicle_type_id}")
async def update_vehicle_type(
    vehicle_type_id: str,
    data:            VehicleTypeConfigUpdate,
    current_user:    User    = Depends(get_current_user),
    db:              Session = Depends(get_db),
):
    vt = db.get(VehicleTypeConfig, uuid.UUID(vehicle_type_id))
    if not vt:
        raise HTTPException(status_code=404, detail="Tipo de vehículo no encontrado")

    if data.name            is not None: vt.name            = data.name
    if data.description     is not None: vt.description     = data.description
    if data.peso_max_lbs    is not None: vt.peso_max_lbs    = data.peso_max_lbs
    if data.volumen_max_ft3 is not None: vt.volumen_max_ft3 = data.volumen_max_ft3
    if data.habilitado      is not None: vt.habilitado      = data.habilitado
    if data.maneja_unidades is not None: vt.maneja_unidades = data.maneja_unidades
    if data.unidades_max    is not None: vt.unidades_max    = data.unidades_max

    db.commit()
    return serialize_vehicle_type(vt)



# ─── Service Types (catálogo) ─────────────────────────────────────────────────

class ServiceTypeCreate(BaseModel):
    code:             str
    name:             str
    description:      Optional[str] = None
    porcentaje_muevo: float = 5.00
    importe_minimo:   float = 0.00
    importe_maximo:   Optional[float] = None
    precio_servicio:  float = 0.00
    precio_por_km:    float = Field(0.00, ge=0)
    precio_por_lb:    float = Field(0.00, ge=0)
    precio_por_ft3:   float = Field(0.00, ge=0)
    habilitado:       bool  = True


class ServiceTypeUpdate(BaseModel):
    name:             Optional[str]   = None
    description:      Optional[str]   = None
    porcentaje_muevo: Optional[float] = None
    importe_minimo:   Optional[float] = None
    importe_maximo:   Optional[float] = None
    precio_servicio:  Optional[float] = None
    precio_por_km:    Optional[float] = Field(None, ge=0)
    precio_por_lb:    Optional[float] = Field(None, ge=0)
    precio_por_ft3:   Optional[float] = Field(None, ge=0)
    habilitado:       Optional[bool]  = None


def serialize_service_type(st: ServiceType) -> dict:
    return {
        "id":               str(st.id),
        "code":             st.code,
        "name":             st.name,
        "description":      st.description,
        "porcentaje_muevo": float(st.porcentaje_muevo),
        "importe_minimo":   float(st.importe_minimo),
        "importe_maximo":   float(st.importe_maximo) if st.importe_maximo else None,
        "precio_servicio":  float(st.precio_servicio),
        "precio_por_km":    float(st.precio_por_km or 0),
        "precio_por_lb":    float(st.precio_por_lb or 0),
        "precio_por_ft3":   float(st.precio_por_ft3 or 0),
        "habilitado":       st.habilitado,
        "created_at":       st.created_at.isoformat() if st.created_at else None,
        "updated_at":       st.updated_at.isoformat() if st.updated_at else None,
    }


@app.get("/api/v1/service-types")
async def list_service_types(
    habilitado:   Optional[bool] = None,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Lista todos los tipos de servicio, opcionalmente filtrando por habilitado."""
    query = select(ServiceType).order_by(ServiceType.name)
    if habilitado is not None:
        query = query.where(ServiceType.habilitado == habilitado)
    types = db.execute(query).scalars().all()
    return [serialize_service_type(st) for st in types]


@app.get("/api/v1/service-types/{service_type_id}")
async def get_service_type(
    service_type_id: str,
    current_user:    User    = Depends(get_current_user),
    db:              Session = Depends(get_db),
):
    st = db.get(ServiceType, uuid.UUID(service_type_id))
    if not st:
        raise HTTPException(status_code=404, detail="Tipo de servicio no encontrado")
    return serialize_service_type(st)


@app.post("/api/v1/service-types", status_code=201)
async def create_service_type(
    data:         ServiceTypeCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Crea un nuevo tipo de servicio (solo super-admin del holding)."""
    require_permission(current_user, db, "config", "create")
    existing = db.execute(
        select(ServiceType).where(ServiceType.code == data.code)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"Ya existe un tipo con código '{data.code}'")

    st = ServiceType(
        id=uuid.uuid4(), code=data.code, name=data.name,
        description=data.description,
        porcentaje_muevo=data.porcentaje_muevo,
        importe_minimo=data.importe_minimo,
        importe_maximo=data.importe_maximo,
        precio_servicio=data.precio_servicio,
        precio_por_km=data.precio_por_km,
        precio_por_lb=data.precio_por_lb,
        precio_por_ft3=data.precio_por_ft3,
        habilitado=data.habilitado,
    )
    db.add(st)
    db.commit()
    return serialize_service_type(st)


@app.patch("/api/v1/service-types/{service_type_id}")
async def update_service_type(
    service_type_id: str,
    data:            ServiceTypeUpdate,
    current_user:    User    = Depends(get_current_user),
    db:              Session = Depends(get_db),
):
    """Actualiza un tipo de servicio existente."""
    require_permission(current_user, db, "config", "edit")
    st = db.get(ServiceType, uuid.UUID(service_type_id))
    if not st:
        raise HTTPException(status_code=404, detail="Tipo de servicio no encontrado")

    if data.name             is not None: st.name             = data.name
    if data.description      is not None: st.description      = data.description
    if data.porcentaje_muevo is not None: st.porcentaje_muevo = data.porcentaje_muevo
    if data.importe_minimo   is not None: st.importe_minimo   = data.importe_minimo
    if data.importe_maximo   is not None: st.importe_maximo   = data.importe_maximo
    if data.precio_servicio  is not None: st.precio_servicio  = data.precio_servicio
    if data.precio_por_km    is not None: st.precio_por_km    = data.precio_por_km
    if data.precio_por_lb    is not None: st.precio_por_lb    = data.precio_por_lb
    if data.precio_por_ft3   is not None: st.precio_por_ft3   = data.precio_por_ft3
    if data.habilitado       is not None: st.habilitado       = data.habilitado

    db.commit()
    return serialize_service_type(st)



# ─── Email configuration ──────────────────────────────────────────────────────

SMTP_HOST     = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER",     "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM     = os.getenv("SMTP_FROM",     "noreply@muevo.app")
SMTP_ENABLED  = bool(SMTP_USER and SMTP_PASSWORD)


async def send_email(to_email: str, subject: str, body_html: str):
    """
    Envía un email de notificación al cliente del lote.
    Si SMTP no está configurado, loguea el mensaje en consola (modo dev).
    """
    if not SMTP_ENABLED:
        print(f"[EMAIL DEV] To: {to_email} | Subject: {subject}")
        print(f"[EMAIL DEV] Body: {body_html[:200]}...")
        return

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text      import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = to_email
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
    except Exception as e:
        print(f"[EMAIL ERROR] {to_email}: {e}")


def email_html(title: str, body_lines: list[str], cta_label: str = None, cta_url: str = None) -> str:
    """Genera el HTML del email con la paleta visual de Muevo."""
    cta_block = ""
    if cta_label and cta_url:
        cta_block = f"""
        <div style="text-align:center;margin:24px 0">
          <a href="{cta_url}" style="background:#F47B20;color:#111;padding:12px 28px;
             border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
            {cta_label}
          </a>
        </div>"""

    rows = "".join(
        f'<p style="margin:8px 0;font-size:14px;color:#333">{line}</p>'
        for line in body_lines
    )

    return f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
                max-width:560px;margin:0 auto;background:#ffffff">
      <div style="background:#0D0D0D;padding:20px 28px;border-radius:8px 8px 0 0">
        <span style="font-size:22px;font-weight:700;color:#F47B20">muevo</span>
        <span style="font-size:13px;color:#6B6B6B;margin-left:12px">Notificación de envío</span>
      </div>
      <div style="padding:28px;border:1px solid #E5E5E5;border-top:none;border-radius:0 0 8px 8px">
        <h2 style="font-size:18px;font-weight:700;color:#0D0D0D;margin:0 0 16px">{title}</h2>
        {rows}
        {cta_block}
        <hr style="border:none;border-top:1px solid #E5E5E5;margin:24px 0">
        <p style="font-size:11px;color:#9B9B9B;margin:0">
          Este email fue enviado automáticamente por Muevo Logistics.<br>
          Si tiene preguntas, contáctenos en soporte@muevo.app
        </p>
      </div>
    </div>"""


async def notify_batch_client(
    batch_id: uuid.UUID,
    event:    str,       # "batch_status" | "stop_status" | "delivery" | "incident"
    subject:  str,
    body_lines: list[str],
    db:       Session,
):
    """
    Busca el cliente asociado al lote y le envía un email si tiene
    habilitadas las notificaciones para ese evento.
    """
    batch = db.get(RouteBatch, batch_id)
    if not batch or not batch.client_id:
        return

    client = db.get(BatchClient, batch.client_id)
    if not client or not client.is_active:
        return

    pref_map = {
        "batch_status": client.notify_batch_status,
        "stop_status":  client.notify_stop_status,
        "delivery":     client.notify_delivery,
        "incident":     client.notify_incident,
    }
    if not pref_map.get(event, False):
        return

    html = email_html(subject, body_lines)
    await send_email(client.email, subject, html)


# ─── Batch Clients CRUD ───────────────────────────────────────────────────────

class BatchClientCreate(BaseModel):
    name:                str
    email:               str
    phone:               Optional[str] = None
    codigo_cliente:      Optional[str] = None
    direccion:           Optional[str] = None
    ciudad:              Optional[str] = None
    estado:              Optional[str] = None
    notify_batch_status: bool = True
    notify_stop_status:  bool = False
    notify_delivery:     bool = True
    notify_incident:     bool = True

class BatchClientUpdate(BaseModel):
    name:                Optional[str]  = None
    email:               Optional[str]  = None
    phone:               Optional[str]  = None
    codigo_cliente:      Optional[str]  = None
    direccion:           Optional[str]  = None
    ciudad:              Optional[str]  = None
    estado:              Optional[str]  = None
    push_token:          Optional[str]  = None
    notify_batch_status: Optional[bool] = None
    notify_stop_status:  Optional[bool] = None
    notify_delivery:     Optional[bool] = None
    notify_incident:     Optional[bool] = None
    is_active:           Optional[bool] = None


def serialize_batch_client(c: BatchClient, db: Session = None) -> dict:
    # Métricas del cliente
    batches = db.execute(
        select(RouteBatch).where(RouteBatch.client_id == c.id)
    ).scalars().all() if db else []
    total_batches    = len(batches)
    total_cost       = sum(
        sum(float(r.gross_pay or 0) for r in (db.execute(
            select(RouteHeader).join(RouteBatchItem, RouteBatchItem.route_header_id == RouteHeader.id)
            .where(RouteBatchItem.batch_id == b.id)
        ).scalars().all() if db else []))
        for b in batches
    )
    completed = sum(1 for b in batches if b.status and b.status.value == 'completed')

    return {
        "id":                   str(c.id),
        "company_id":           str(c.company_id),
        "name":                 c.name,
        "email":                c.email,
        "phone":                c.phone,
        "codigo_cliente":       c.codigo_cliente,
        "direccion":            c.direccion,
        "ciudad":               c.ciudad,
        "estado":               c.estado,
        "push_token":           c.push_token,
        "notify_batch_status":  c.notify_batch_status,
        "notify_stop_status":   c.notify_stop_status,
        "notify_delivery":      c.notify_delivery,
        "notify_incident":      c.notify_incident,
        "is_active":            c.is_active,
        "created_at":           c.created_at.isoformat() if c.created_at else None,
        "metrics": {
            "total_batches":    total_batches,
            "completed_batches":completed,
            "total_cost":       round(total_cost, 2),
        },
    }


@app.get("/api/v1/warehouse/clients")
async def list_batch_clients(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Lista los contactos/clientes registrados para la compañía activa."""
    company = get_current_company(current_user, db, x_company_id)
    clients = db.execute(
        select(BatchClient)
        .where(BatchClient.company_id == company.id)
        .order_by(BatchClient.name)
    ).scalars().all()
    return [serialize_batch_client(c, db) for c in clients]


@app.post("/api/v1/warehouse/clients", status_code=201)
async def create_batch_client(
    data:         BatchClientCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Registra un nuevo contacto/cliente para la compañía activa."""
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "clients", "create", company.id)
    client = BatchClient(
        id=uuid.uuid4(), company_id=company.id,
        name=data.name, email=data.email, phone=data.phone,
        codigo_cliente=data.codigo_cliente,
        direccion=data.direccion, ciudad=data.ciudad, estado=data.estado,
        notify_batch_status=data.notify_batch_status,
        notify_stop_status=data.notify_stop_status,
        notify_delivery=data.notify_delivery,
        notify_incident=data.notify_incident,
    )
    db.add(client)
    db.commit()
    return serialize_batch_client(client, db)


@app.patch("/api/v1/warehouse/clients/{client_id}")
async def update_batch_client(
    client_id:    str,
    data:         BatchClientUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Actualiza datos o preferencias de notificación de un cliente."""
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "clients", "edit", company.id)
    client  = db.get(BatchClient, uuid.UUID(client_id))
    if not client or client.company_id != company.id:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    if data.name                is not None: client.name                = data.name
    if data.email               is not None: client.email               = data.email
    if data.phone               is not None: client.phone               = data.phone
    if data.codigo_cliente      is not None: client.codigo_cliente      = data.codigo_cliente
    if data.direccion           is not None: client.direccion           = data.direccion
    if data.ciudad              is not None: client.ciudad              = data.ciudad
    if data.estado              is not None: client.estado              = data.estado
    if data.push_token          is not None: client.push_token          = data.push_token
    if data.notify_batch_status is not None: client.notify_batch_status = data.notify_batch_status
    if data.notify_stop_status  is not None: client.notify_stop_status  = data.notify_stop_status
    if data.notify_delivery     is not None: client.notify_delivery     = data.notify_delivery
    if data.notify_incident     is not None: client.notify_incident     = data.notify_incident
    if data.is_active           is not None: client.is_active           = data.is_active

    db.commit()
    return serialize_batch_client(client, db)


@app.delete("/api/v1/warehouse/clients/{client_id}", status_code=204)
async def delete_batch_client(
    client_id:    str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Elimina un cliente (solo si no tiene lotes activos asociados)."""
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "clients", "delete", company.id)
    client  = db.get(BatchClient, uuid.UUID(client_id))
    if not client or client.company_id != company.id:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    # Soft delete — desactivar en vez de borrar
    client.is_active = False
    db.commit()



@app.post("/api/v1/warehouse/clients/import-csv", status_code=201)
async def import_clients_csv(
    data:         dict,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """
    Importa clientes desde CSV parseado en el frontend.
    Espera: { "rows": [{name, email, phone, codigo_cliente, direccion, ciudad, estado}, ...] }
    Si el email ya existe para la compañía, actualiza en vez de duplicar.
    """
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "clients", "create", company.id)
    rows    = data.get("rows", [])
    created = 0
    updated = 0

    for row in rows:
        email = (row.get("email") or "").strip().lower()
        if not email or not row.get("name"):
            continue

        existing = db.execute(
            select(BatchClient).where(
                BatchClient.company_id == company.id,
                BatchClient.email      == email,
            )
        ).scalar_one_or_none()

        if existing:
            existing.name           = row.get("name",           existing.name)
            existing.phone          = row.get("phone")          or existing.phone
            existing.codigo_cliente = row.get("codigo_cliente") or existing.codigo_cliente
            existing.direccion      = row.get("direccion")      or existing.direccion
            existing.ciudad         = row.get("ciudad")         or existing.ciudad
            existing.estado         = row.get("estado")         or existing.estado
            updated += 1
        else:
            db.add(BatchClient(
                id=uuid.uuid4(), company_id=company.id,
                name=row.get("name"), email=email,
                phone=row.get("phone"),
                codigo_cliente=row.get("codigo_cliente"),
                direccion=row.get("direccion"),
                ciudad=row.get("ciudad"),
                estado=row.get("estado"),
            ))
            created += 1

    db.commit()
    return {"status": "imported", "created": created, "updated": updated}


@app.get("/api/v1/warehouse/clients/{client_id}/batches")
async def get_client_batches(
    client_id:    str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Historial de lotes asociados a un cliente."""
    company = get_current_company(current_user, db, x_company_id)
    client  = db.get(BatchClient, uuid.UUID(client_id))
    if not client or client.company_id != company.id:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    batches = db.execute(
        select(RouteBatch)
        .where(RouteBatch.client_id == uuid.UUID(client_id))
        .order_by(RouteBatch.created_at.desc())
    ).scalars().all()

    result = []
    for b in batches:
        routes = db.execute(
            select(RouteHeader)
            .join(RouteBatchItem, RouteBatchItem.route_header_id == RouteHeader.id)
            .where(RouteBatchItem.batch_id == b.id)
        ).scalars().all()
        total_cost = sum(float(r.gross_pay or 0) for r in routes)
        completed  = sum(1 for r in routes if r.status and r.status.value == 'completed')
        result.append({
            "id":           str(b.id),
            "notes":        b.notes,
            "status":       b.status.value if b.status else None,
            "total_routes": len(routes),
            "completed_routes": completed,
            "total_cost":   round(total_cost, 2),
            "created_at":   b.created_at.isoformat() if b.created_at else None,
        })
    return result

@app.post("/api/v1/warehouse/clients/{client_id}/test-notification")
async def test_client_notification(
    client_id:    str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Envía un email de prueba al cliente para verificar la configuración."""
    company = get_current_company(current_user, db, x_company_id)
    client  = db.get(BatchClient, uuid.UUID(client_id))
    if not client or client.company_id != company.id:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    await send_email(
        to_email=client.email,
        subject="✅ Prueba de notificación — Muevo",
        body_html=email_html(
            "Notificaciones configuradas correctamente",
            [
                f"Hola <strong>{client.name}</strong>,",
                "Este es un email de prueba de <strong>Muevo Logistics</strong>.",
                "Recibirás notificaciones automáticas según tus preferencias:",
                f"• Cambios de estado del lote: {'✓' if client.notify_batch_status else '✗'}",
                f"• Cambios de estado de paradas: {'✓' if client.notify_stop_status else '✗'}",
                f"• Entrega confirmada: {'✓' if client.notify_delivery else '✗'}",
                f"• Incidencias reportadas: {'✓' if client.notify_incident else '✗'}",
            ]
        )
    )
    return {"status": "sent", "to": client.email}


# ─── Holding user management ──────────────────────────────────────────────────

def get_current_holding_user(current_user: User, db: Session) -> HoldingUser:
    """Resuelve el HoldingUser del usuario autenticado. Lanza 403 si no existe."""
    hu = db.execute(
        select(HoldingUser).where(
            HoldingUser.user_id == current_user.id,
            HoldingUser.is_active == True,
        )
    ).scalar_one_or_none()
    if not hu:
        raise HTTPException(status_code=403, detail="Usuario no pertenece a ningún holding")
    return hu


def require_super_admin(hu: HoldingUser):
    if not hu.is_super_admin:
        raise HTTPException(status_code=403, detail="Se requiere rol de super-admin del holding")


def require_permission(
    current_user: User, db: Session, module: str, action: str,
    company_id: Optional[uuid.UUID] = None,
) -> None:
    """
    Verifica que el usuario tenga el permiso '{module}.{action}' a través de
    algún perfil que tenga asignado (ver HoldingUserProfile / ProfilePermission).

    - super_admin del holding siempre pasa.
    - Un HoldingUserProfile con company_id=NULL aplica a todo el holding;
      con company_id=X aplica solo a esa compañía.
    - Si el usuario no tiene ningún HoldingUserProfile asignado (todavía no
      migrado al nuevo sistema), se lo deja pasar — el resto del endpoint
      sigue protegido por los chequeos de acceso existentes
      (get_current_company / HoldingUserCompany / CompanyAdmin). Esto evita
      romper cuentas que aún no tienen un perfil formal asignado.
    """
    hu = get_current_holding_user(current_user, db)
    if hu.is_super_admin:
        return

    assignments = db.execute(
        select(HoldingUserProfile).where(HoldingUserProfile.holding_user_id == hu.id)
    ).scalars().all()
    if not assignments:
        return  # sin perfil asignado todavía — no bloquear, ver docstring

    code = f"{module}.{action}"
    for a in assignments:
        if company_id is None:
            # Recurso global (ej. catálogos de config) — solo cuenta una
            # asignación de todo el holding, no una acotada a una compañía.
            if a.company_id is not None:
                continue
        else:
            # Recurso de una compañía — cuenta una asignación holding-wide
            # (company_id NULL) o una específica de esa compañía.
            if a.company_id is not None and a.company_id != company_id:
                continue
        exists = db.execute(
            select(Permission.id)
            .join(ProfilePermission, ProfilePermission.permission_id == Permission.id)
            .where(ProfilePermission.profile_id == a.profile_id, Permission.code == code)
        ).first()
        if exists:
            return

    raise HTTPException(status_code=403, detail=f"Sin permiso '{code}' para esta acción")


def resolve_active_company(request_company_id: str, hu: HoldingUser, db: Session) -> tuple:
    """
    Valida que el company_id que viene en el header X-Company-ID esté
    autorizado para este holding user y devuelve (company, access).
    """
    cid = uuid.UUID(request_company_id)

    # Super-admin tiene acceso a todas las compañías del holding
    if hu.is_super_admin:
        company = db.execute(
            select(Company).where(Company.id == cid, Company.holding_id == hu.holding_id)
        ).scalar_one_or_none()
        if not company:
            raise HTTPException(status_code=403, detail="Compañía no pertenece al holding")
        return company, None

    access = db.execute(
        select(HoldingUserCompany).where(
            HoldingUserCompany.holding_user_id == hu.id,
            HoldingUserCompany.company_id == cid,
            HoldingUserCompany.can_view == True,
        )
    ).scalar_one_or_none()
    if not access:
        raise HTTPException(status_code=403, detail="Sin acceso a esta compañía")
    company = db.get(Company, cid)
    return company, access


class HoldingUserCreate(BaseModel):
    email:        str
    full_name:    str
    password:     str
    role:         str = "operator"
    is_super_admin: bool = False
    company_access: list[dict] = []
    # [{"company_id": "...", "can_view": true, "can_operate": false, "can_invoice": false, "is_admin": false}]


class HoldingUserUpdate(BaseModel):
    role:           Optional[str]  = None
    is_super_admin: Optional[bool] = None
    is_active:      Optional[bool] = None
    company_access: Optional[list[dict]] = None


@app.get("/api/v1/holding/companies")
async def list_accessible_companies(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Lista las compañías accesibles al usuario autenticado (con sus permisos)."""
    hu = get_current_holding_user(current_user, db)
    portal_data = resolve_user_portal(current_user, db)
    return {"companies": portal_data.get("companies", []), "is_super_admin": hu.is_super_admin}


@app.get("/api/v1/holding/users")
async def list_holding_users(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Lista todos los usuarios del holding (solo super-admin)."""
    hu = get_current_holding_user(current_user, db)
    require_super_admin(hu)

    users = db.execute(
        select(HoldingUser).where(HoldingUser.holding_id == hu.holding_id)
    ).scalars().all()

    result = []
    for h in users:
        user = db.get(User, h.user_id)
        accesses = db.execute(
            select(HoldingUserCompany).where(HoldingUserCompany.holding_user_id == h.id)
        ).scalars().all()
        companies = []
        for acc in accesses:
            company = db.get(Company, acc.company_id)
            companies.append({
                "id": str(acc.company_id),
                "name": company.name if company else "",
                "can_view": acc.can_view, "can_operate": acc.can_operate,
                "can_invoice": acc.can_invoice, "is_admin": acc.is_admin,
            })
        result.append({
            "holding_user_id": str(h.id),
            "user_id":         str(h.user_id),
            "full_name":       user.full_name if user else "",
            "email":           user.email    if user else "",
            "role":            h.role,
            "is_super_admin":  h.is_super_admin,
            "is_active":       h.is_active,
            "companies":       companies,
            "created_at":      h.created_at.isoformat() if h.created_at else None,
        })
    return result


@app.post("/api/v1/holding/users", status_code=201)
async def create_holding_user(
    data:         HoldingUserCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Crea un usuario nuevo en el holding con acceso a las compañías indicadas (solo super-admin)."""
    hu = get_current_holding_user(current_user, db)
    require_super_admin(hu)

    # Verificar que el email no exista
    existing = db.execute(select(User).where(User.email == data.email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"El email {data.email} ya está registrado")

    # Crear usuario
    new_user = User(
        id=uuid.uuid4(), email=data.email, full_name=data.full_name,
        hashed_password=hash_password(data.password),
        role=UserRole.DRIVER,  # rol técnico; el acceso real está en holding_user_companies
        is_active=True,
    )
    db.add(new_user)
    db.flush()

    # Crear HoldingUser
    new_hu = HoldingUser(
        id=uuid.uuid4(), holding_id=hu.holding_id, user_id=new_user.id,
        role=data.role, is_super_admin=data.is_super_admin, is_active=True,
    )
    db.add(new_hu)
    db.flush()

    # Asignar accesos a compañías
    for acc_data in data.company_access:
        cid = uuid.UUID(acc_data["company_id"])
        # Verificar que la compañía pertenezca al holding
        company = db.execute(
            select(Company).where(Company.id == cid, Company.holding_id == hu.holding_id)
        ).scalar_one_or_none()
        if not company:
            continue
        db.add(HoldingUserCompany(
            id=uuid.uuid4(), holding_user_id=new_hu.id, company_id=cid,
            can_view=acc_data.get("can_view", True),
            can_operate=acc_data.get("can_operate", False),
            can_invoice=acc_data.get("can_invoice", False),
            is_admin=acc_data.get("is_admin", False),
            granted_by_id=current_user.id,
        ))

    db.commit()
    return {"status": "created", "user_id": str(new_user.id), "holding_user_id": str(new_hu.id)}


@app.patch("/api/v1/holding/users/{holding_user_id}")
async def update_holding_user(
    holding_user_id: str,
    data:            HoldingUserUpdate,
    current_user:    User    = Depends(get_current_user),
    db:              Session = Depends(get_db),
):
    """Edita el rol, estado o accesos a compañías de un usuario del holding (solo super-admin)."""
    hu = get_current_holding_user(current_user, db)
    require_super_admin(hu)

    target = db.get(HoldingUser, uuid.UUID(holding_user_id))
    if not target or target.holding_id != hu.holding_id:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en este holding")

    if data.role           is not None: target.role           = data.role
    if data.is_super_admin is not None: target.is_super_admin = data.is_super_admin
    if data.is_active      is not None: target.is_active      = data.is_active

    if data.company_access is not None:
        # Reemplazar todos los accesos
        db.execute(
            sa_delete(HoldingUserCompany).where(HoldingUserCompany.holding_user_id == target.id)
        )
        db.flush()
        for acc_data in data.company_access:
            cid = uuid.UUID(acc_data["company_id"])
            company = db.execute(
                select(Company).where(Company.id == cid, Company.holding_id == hu.holding_id)
            ).scalar_one_or_none()
            if not company:
                continue
            db.add(HoldingUserCompany(
                id=uuid.uuid4(), holding_user_id=target.id, company_id=cid,
                can_view=acc_data.get("can_view", True),
                can_operate=acc_data.get("can_operate", False),
                can_invoice=acc_data.get("can_invoice", False),
                is_admin=acc_data.get("is_admin", False),
                granted_by_id=current_user.id,
            ))

    db.commit()
    return {"status": "updated", "holding_user_id": holding_user_id}


@app.delete("/api/v1/holding/users/{holding_user_id}", status_code=204)
async def deactivate_holding_user(
    holding_user_id: str,
    current_user:    User    = Depends(get_current_user),
    db:              Session = Depends(get_db),
):
    """Desactiva un usuario del holding (soft delete — no borra el User de la DB)."""
    hu = get_current_holding_user(current_user, db)
    require_super_admin(hu)

    target = db.get(HoldingUser, uuid.UUID(holding_user_id))
    if not target or target.holding_id != hu.holding_id:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    target.is_active = False
    db.commit()


# ─── Admin — serve admin.html ─────────────────────────────────────────────────

@app.get("/admin.html", include_in_schema=False)
async def serve_admin_portal():
    return FileResponse("admin.html", media_type="text/html")


# ─── Admin — Modules & Actions (read-only catalog) ───────────────────────────

@app.get("/api/v1/admin/modules")
async def list_modules(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    mods = db.execute(select(Module).order_by(Module.sort_order)).scalars().all()
    return [{"id": str(m.id), "code": m.code, "name": m.name,
             "description": m.description} for m in mods]


@app.get("/api/v1/admin/actions")
async def list_actions(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    acts = db.execute(select(Action).order_by(Action.sort_order)).scalars().all()
    return [{"id": str(a.id), "code": a.code, "name": a.name,
             "description": a.description} for a in acts]


@app.get("/api/v1/admin/permissions")
async def list_permissions(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    perms = db.execute(select(Permission).where(Permission.is_active == True)).scalars().all()
    return [{
        "id": str(p.id), "code": p.code, "name": p.name,
        "module_id": str(p.module_id), "module_code": p.module.code if p.module else None,
        "action_id": str(p.action_id), "action_code": p.action.code if p.action else None,
    } for p in perms]


# ─── Admin — Profiles ─────────────────────────────────────────────────────────

class ProfileCreate(BaseModel):
    code:        str
    name:        str
    description: Optional[str] = None
    permission_ids: list[str]  = []

class ProfileUpdate(BaseModel):
    name:           Optional[str]      = None
    description:    Optional[str]      = None
    is_active:      Optional[bool]     = None
    permission_ids: Optional[list[str]] = None


def serialize_profile(p: Profile, db: Session) -> dict:
    perms = db.execute(
        select(Permission).join(ProfilePermission)
        .where(ProfilePermission.profile_id == p.id)
    ).scalars().all()
    return {
        "id": str(p.id), "code": p.code, "name": p.name,
        "description": p.description, "is_system": p.is_system,
        "is_active": p.is_active,
        "permissions": [{"id": str(pm.id), "code": pm.code, "name": pm.name} for pm in perms],
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def serialize_audit_log(a: AuditLog, db: Session) -> dict:
    user = db.get(User, a.changed_by_user_id) if a.changed_by_user_id else None
    return {
        "id":              str(a.id),
        "table_name":      a.table_name,
        "record_id":       a.record_id,
        "action":          a.action.value if hasattr(a.action, 'value') else a.action,
        "changed_by":      {"id": str(user.id), "name": user.full_name, "email": user.email} if user else None,
        "company_id":      str(a.company_id) if a.company_id else None,
        "changes":         a.changes,
        "created_at":      a.created_at.isoformat() if a.created_at else None,
    }


@app.get("/api/v1/admin/audit-logs")
async def list_audit_logs(
    table_name:         Optional[str] = None,
    record_id:          Optional[str] = None,
    changed_by_user_id: Optional[str] = None,
    date_from:          Optional[str] = None,
    date_to:            Optional[str] = None,
    limit:              int = 50,
    offset:             int = 0,
    current_user:       User    = Depends(get_current_user),
    db:                 Session = Depends(get_db),
):
    """
    Historial de auditoría — captura automática (ver listener before_flush
    en models.py) de cualquier insert/update/delete que pasó por el ORM.
    Alcance exclusivamente holding-wide: no requiere una company en particular.
    """
    require_permission(current_user, db, "audit", "view", company_id=None)

    limit = max(1, min(limit, 200))
    query = select(AuditLog)
    if table_name:
        query = query.where(AuditLog.table_name == table_name)
    if record_id:
        query = query.where(AuditLog.record_id == record_id)
    if changed_by_user_id:
        query = query.where(AuditLog.changed_by_user_id == uuid.UUID(changed_by_user_id))
    if date_from:
        query = query.where(AuditLog.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.where(AuditLog.created_at <= datetime.fromisoformat(date_to))

    query = query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    rows = db.execute(query).scalars().all()
    return [serialize_audit_log(a, db) for a in rows]


@app.get("/api/v1/admin/profiles")
async def list_profiles(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    profiles = db.execute(select(Profile).order_by(Profile.name)).scalars().all()
    return [serialize_profile(p, db) for p in profiles]


@app.post("/api/v1/admin/profiles", status_code=201)
async def create_profile(
    data:         ProfileCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    existing = db.execute(select(Profile).where(Profile.code == data.code)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"Ya existe un perfil con código '{data.code}'")

    profile = Profile(id=uuid.uuid4(), code=data.code, name=data.name,
                      description=data.description, is_system=False)
    db.add(profile)
    db.flush()

    for pid in data.permission_ids:
        perm = db.get(Permission, uuid.UUID(pid))
        if perm:
            db.add(ProfilePermission(id=uuid.uuid4(), profile_id=profile.id,
                                     permission_id=perm.id))
    db.commit()
    return serialize_profile(profile, db)


@app.patch("/api/v1/admin/profiles/{profile_id}")
async def update_profile(
    profile_id:   str,
    data:         ProfileUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    profile = db.get(Profile, uuid.UUID(profile_id))
    if not profile:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")

    if data.name        is not None: profile.name        = data.name
    if data.description is not None: profile.description = data.description
    if data.is_active   is not None: profile.is_active   = data.is_active

    if data.permission_ids is not None:
        # Reemplazar todos los permisos
        db.execute(sa_delete(ProfilePermission).where(
            ProfilePermission.profile_id == profile.id))
        db.flush()
        for pid in data.permission_ids:
            perm = db.get(Permission, uuid.UUID(pid))
            if perm:
                db.add(ProfilePermission(id=uuid.uuid4(), profile_id=profile.id,
                                         permission_id=perm.id))
    db.commit()
    return serialize_profile(profile, db)


# ─── Admin — Companies ────────────────────────────────────────────────────────

class CompanyCreate(BaseModel):
    name:               str
    industry:           Optional[str] = None
    contact_email:      Optional[str] = None
    contact_phone:      Optional[str] = None
    city:               Optional[str] = None
    state:              Optional[str] = None
    country:            str = "US"
    allowed_modes:      list[str] = []
    payment_terms_days: int = 30
    max_csv_rows:          Optional[int] = Field(None, ge=1)
    max_csv_file_size_mb:  Optional[int] = Field(None, ge=1)

class CompanyUpdate(BaseModel):
    name:               Optional[str]      = None
    industry:           Optional[str]      = None
    contact_email:      Optional[str]      = None
    contact_phone:      Optional[str]      = None
    city:               Optional[str]      = None
    state:              Optional[str]      = None
    country:            Optional[str]      = None
    allowed_modes:      Optional[list[str]] = None
    payment_terms_days: Optional[int]      = None
    is_active:          Optional[bool]     = None
    max_csv_rows:          Optional[int] = Field(None, ge=1)
    max_csv_file_size_mb:  Optional[int] = Field(None, ge=1)


def serialize_company(c: Company, db: Session) -> dict:
    wh_count = db.execute(
        select(func.count(Warehouse.id)).where(Warehouse.company_id == c.id)
    ).scalar()
    return {
        "id": str(c.id), "name": c.name, "industry": c.industry,
        "contact_email": c.contact_email,
        "city": c.city, "state": c.state, "country": c.country,
        "allowed_modes": c.allowed_modes or [],
        "payment_terms_days": c.payment_terms_days,
        "is_active": getattr(c, 'is_active', True),
        "warehouse_count": wh_count,
        "holding_id": str(c.holding_id) if c.holding_id else None,
        # Límites de CSV — el valor efectivo (custom de la company, o el default global)
        "max_csv_rows":            c.max_csv_rows or DEFAULT_MAX_CSV_ROWS,
        "max_csv_file_size_mb":    c.max_csv_file_size_mb or DEFAULT_MAX_CSV_FILE_SIZE_MB,
        "max_csv_rows_custom":     c.max_csv_rows,           # None = usa el default
        "max_csv_file_size_mb_custom": c.max_csv_file_size_mb,
    }


@app.get("/api/v1/admin/companies")
async def admin_list_companies(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Lista compañías del holding del usuario autenticado."""
    hu = get_current_holding_user(current_user, db)
    companies = db.execute(
        select(Company).where(Company.holding_id == hu.holding_id)
        .order_by(Company.name)
    ).scalars().all()
    return [serialize_company(c, db) for c in companies]


@app.post("/api/v1/admin/companies", status_code=201)
async def admin_create_company(
    data:         CompanyCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    hu = get_current_holding_user(current_user, db)
    require_super_admin(hu)

    company = Company(
        id=uuid.uuid4(), holding_id=hu.holding_id,
        name=data.name, industry=data.industry,
        contact_email=data.contact_email,
        city=data.city, state=data.state, country=data.country,
        allowed_modes=data.allowed_modes,
        payment_terms_days=data.payment_terms_days,
        is_primary_company=False,
        max_csv_rows=data.max_csv_rows,
        max_csv_file_size_mb=data.max_csv_file_size_mb,
    )
    db.add(company)
    db.commit()
    return serialize_company(company, db)


@app.patch("/api/v1/admin/companies/{company_id}")
async def admin_update_company(
    company_id:   str,
    data:         CompanyUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    hu = get_current_holding_user(current_user, db)
    company = db.get(Company, uuid.UUID(company_id))
    if not company or company.holding_id != hu.holding_id:
        raise HTTPException(status_code=404, detail="Compañía no encontrada")

    if data.name               is not None: company.name               = data.name
    if data.industry           is not None: company.industry           = data.industry
    if data.contact_email      is not None: company.contact_email      = data.contact_email
    if data.city               is not None: company.city               = data.city
    if data.state              is not None: company.state              = data.state
    if data.country            is not None: company.country            = data.country
    if data.allowed_modes      is not None: company.allowed_modes      = data.allowed_modes
    if data.payment_terms_days is not None: company.payment_terms_days = data.payment_terms_days
    if data.max_csv_rows         is not None: company.max_csv_rows         = data.max_csv_rows
    if data.max_csv_file_size_mb is not None: company.max_csv_file_size_mb = data.max_csv_file_size_mb

    db.commit()
    return serialize_company(company, db)


# ─── Admin — Warehouses ───────────────────────────────────────────────────────

class WarehouseCreate(BaseModel):
    code:              str
    name:              str
    address_line1:     str
    city:              str
    state:             str
    lat:               Optional[float] = None
    lng:               Optional[float] = None
    docks_count:       int  = 1
    is_origin_default: bool = False

class WarehouseUpdate(BaseModel):
    name:              Optional[str]   = None
    address_line1:     Optional[str]   = None
    city:              Optional[str]   = None
    state:             Optional[str]   = None
    lat:               Optional[float] = None
    lng:               Optional[float] = None
    docks_count:       Optional[int]   = None
    is_origin_default: Optional[bool]  = None
    is_active:         Optional[bool]  = None


def serialize_warehouse(w: Warehouse) -> dict:
    return {
        "id": str(w.id), "code": w.code, "name": w.name,
        "company_id": str(w.company_id),
        "address_line1": w.address_line1, "city": w.city, "state": w.state,
        "lat": float(w.base_lat) if getattr(w, 'base_lat', None) else None,
        "lng": float(w.base_lng) if getattr(w, 'base_lng', None) else None,
        "docks_count": w.docks_count,
        "is_origin_default": w.is_origin_default,
        "is_active": getattr(w, 'is_active', True),
    }


@app.get("/api/v1/admin/companies/{company_id}/warehouses")
async def admin_list_warehouses(
    company_id:   str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    hu = get_current_holding_user(current_user, db)
    company = db.get(Company, uuid.UUID(company_id))
    if not company or company.holding_id != hu.holding_id:
        raise HTTPException(status_code=404, detail="Compañía no encontrada")

    warehouses = db.execute(
        select(Warehouse).where(Warehouse.company_id == uuid.UUID(company_id))
        .order_by(Warehouse.name)
    ).scalars().all()
    return [serialize_warehouse(w) for w in warehouses]


@app.post("/api/v1/admin/companies/{company_id}/warehouses", status_code=201)
async def admin_create_warehouse(
    company_id:   str,
    data:         WarehouseCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    hu = get_current_holding_user(current_user, db)
    company = db.get(Company, uuid.UUID(company_id))
    if not company or company.holding_id != hu.holding_id:
        raise HTTPException(status_code=404, detail="Compañía no encontrada")

    existing = db.execute(
        select(Warehouse).where(
            Warehouse.company_id == uuid.UUID(company_id),
            Warehouse.code == data.code,
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"Ya existe un warehouse con código '{data.code}' en esta compañía")

    wh = Warehouse(
        id=uuid.uuid4(), company_id=uuid.UUID(company_id),
        code=data.code, name=data.name,
        address_line1=data.address_line1, city=data.city, state=data.state,
        docks_count=data.docks_count,
        is_origin_default=data.is_origin_default,
    )
    if data.lat: wh.base_lat = data.lat
    if data.lng: wh.base_lng = data.lng
    db.add(wh)
    db.commit()
    return serialize_warehouse(wh)


@app.patch("/api/v1/admin/companies/{company_id}/warehouses/{warehouse_id}")
async def admin_update_warehouse(
    company_id:   str,
    warehouse_id: str,
    data:         WarehouseUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    hu = get_current_holding_user(current_user, db)
    company = db.get(Company, uuid.UUID(company_id))
    if not company or company.holding_id != hu.holding_id:
        raise HTTPException(status_code=404, detail="Compañía no encontrada")

    wh = db.get(Warehouse, uuid.UUID(warehouse_id))
    if not wh or wh.company_id != uuid.UUID(company_id):
        raise HTTPException(status_code=404, detail="Warehouse no encontrado")

    if data.name              is not None: wh.name              = data.name
    if data.address_line1     is not None: wh.address_line1     = data.address_line1
    if data.city              is not None: wh.city              = data.city
    if data.state             is not None: wh.state             = data.state
    if data.docks_count       is not None: wh.docks_count       = data.docks_count
    if data.is_origin_default is not None: wh.is_origin_default = data.is_origin_default
    if data.lat               is not None: wh.base_lat          = data.lat
    if data.lng               is not None: wh.base_lng          = data.lng

    db.commit()
    return serialize_warehouse(wh)


# ─── Admin — Holding User Profiles ───────────────────────────────────────────

class AssignProfileRequest(BaseModel):
    holding_user_id: str
    profile_id:      str
    company_id:      Optional[str] = None
    warehouse_id:    Optional[str] = None


@app.get("/api/v1/admin/users/{holding_user_id}/profiles")
async def get_user_profiles(
    holding_user_id: str,
    current_user:    User    = Depends(get_current_user),
    db:              Session = Depends(get_db),
):
    """Lista los perfiles asignados a un usuario del holding."""
    assignments = db.execute(
        select(HoldingUserProfile).where(
            HoldingUserProfile.holding_user_id == uuid.UUID(holding_user_id)
        )
    ).scalars().all()

    result = []
    for a in assignments:
        profile = db.get(Profile, a.profile_id)
        company = db.get(Company, a.company_id) if a.company_id else None
        warehouse = db.get(Warehouse, a.warehouse_id) if a.warehouse_id else None
        result.append({
            "id":           str(a.id),
            "profile_id":   str(a.profile_id),
            "profile_code": profile.code if profile else None,
            "profile_name": profile.name if profile else None,
            "company_id":   str(a.company_id) if a.company_id else None,
            "company_name": company.name if company else None,
            "warehouse_id": str(a.warehouse_id) if a.warehouse_id else None,
            "warehouse_name": warehouse.name if warehouse else None,
            "granted_at":   a.granted_at.isoformat() if a.granted_at else None,
        })
    return result


@app.post("/api/v1/admin/users/profiles", status_code=201)
async def assign_profile(
    data:         AssignProfileRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Asigna un perfil a un usuario del holding."""
    hu = get_current_holding_user(current_user, db)
    require_super_admin(hu)

    assignment = HoldingUserProfile(
        id=uuid.uuid4(),
        holding_user_id=uuid.UUID(data.holding_user_id),
        profile_id=uuid.UUID(data.profile_id),
        company_id=uuid.UUID(data.company_id) if data.company_id else None,
        warehouse_id=uuid.UUID(data.warehouse_id) if data.warehouse_id else None,
        granted_by_id=current_user.id,
    )
    db.add(assignment)
    db.commit()
    return {"status": "assigned", "id": str(assignment.id)}


@app.delete("/api/v1/admin/users/profiles/{assignment_id}", status_code=204)
async def remove_profile_assignment(
    assignment_id: str,
    current_user:  User    = Depends(get_current_user),
    db:            Session = Depends(get_db),
):
    """Elimina una asignación de perfil."""
    hu = get_current_holding_user(current_user, db)
    require_super_admin(hu)

    assignment = db.get(HoldingUserProfile, uuid.UUID(assignment_id))
    if not assignment:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")
    db.delete(assignment)
    db.commit()


# ─── Warehouse Inventory — escaneo de cajas ───────────────────────────────────

def parse_scanned_code(raw: str) -> dict:
    """
    Parsea el código escaneado en 3 formatos:
      1. JSON  — {"destino":..., "dir":..., "lat":..., ...}
      2. Pipe  — CLI-001|Destino|Dirección|lat|lng|peso|vol|contacto|tel
      3. ID    — cualquier otra cosa → solo se guarda como raw_code
    """
    raw = raw.strip()
    result = {
        "raw_code":      raw,
        "codigo_cliente": None,
        "destino":       None,
        "direccion":     None,
        "lat":           None,
        "lng":           None,
        "peso_lbs_unit": 0.0,
        "volumen_ft3_unit": 0.0,
        "contacto":      None,
        "telefono":      None,
        "notas":         None,
    }

    # Formato 1: JSON
    if raw.startswith("{"):
        try:
            import json
            data = json.loads(raw)
            result["codigo_cliente"]   = data.get("cliente") or data.get("codigo_cliente")
            result["destino"]          = data.get("destino")
            result["direccion"]        = data.get("dir") or data.get("direccion")
            result["lat"]              = float(data["lat"])  if "lat"  in data else None
            result["lng"]              = float(data["lng"])  if "lng"  in data else None
            result["peso_lbs_unit"]    = float(data.get("peso", 0))
            result["volumen_ft3_unit"] = float(data.get("vol",  0))
            result["contacto"]         = data.get("contacto")
            result["telefono"]         = data.get("tel") or data.get("telefono")
            result["notas"]            = data.get("notas")
        except Exception:
            pass
        return result

    # Formato 2: Pipe-delimited
    # cliente|destino|direccion|lat|lng|peso|vol|contacto|tel
    if "|" in raw:
        parts = raw.split("|")
        def safe_get(lst, i, cast=str, default=None):
            try: return cast(lst[i]) if lst[i].strip() else default
            except: return default
        result["codigo_cliente"]   = safe_get(parts, 0)
        result["destino"]          = safe_get(parts, 1)
        result["direccion"]        = safe_get(parts, 2)
        result["lat"]              = safe_get(parts, 3, float)
        result["lng"]              = safe_get(parts, 4, float)
        result["peso_lbs_unit"]    = safe_get(parts, 5, float, 0.0)
        result["volumen_ft3_unit"] = safe_get(parts, 6, float, 0.0)
        result["contacto"]         = safe_get(parts, 7)
        result["telefono"]         = safe_get(parts, 8)
        return result

    # Formato 3: Solo ID — guardar como raw_code
    return result


def serialize_inventory_item(item: WarehouseInventoryItem) -> dict:
    return {
        "id":               str(item.id),
        "raw_code":         item.raw_code,
        "codigo_cliente":   item.codigo_cliente,
        "destino":          item.destino,
        "direccion":        item.direccion,
        "lat":              item.lat,
        "lng":              item.lng,
        "peso_lbs_unit":    float(item.peso_lbs_unit),
        "volumen_ft3_unit": float(item.volumen_ft3_unit),
        "peso_lbs_total":   float(item.peso_lbs_total),
        "volumen_ft3_total":float(item.volumen_ft3_total),
        "contacto":         item.contacto,
        "telefono":         item.telefono,
        "notas":            item.notas,
        "quantity":         item.quantity,
        "status":           item.status.value if hasattr(item.status, 'value') else item.status,
        "batch_id":         str(item.batch_id) if item.batch_id else None,
        "scanned_at":       item.scanned_at.isoformat() if item.scanned_at else None,
    }


class ScanRequest(BaseModel):
    raw_code:    str
    batch_id:    Optional[str] = None  # si viene → flujo B (directo a lote)


class InventoryItemUpdate(BaseModel):
    destino:          Optional[str]   = None
    direccion:        Optional[str]   = None
    lat:              Optional[float] = None
    lng:              Optional[float] = None
    peso_lbs_unit:    Optional[float] = None
    volumen_ft3_unit: Optional[float] = None
    contacto:         Optional[str]   = None
    telefono:         Optional[str]   = None
    notas:            Optional[str]   = None
    codigo_cliente:   Optional[str]   = None
    status:           Optional[str]   = None


@app.post("/api/v1/warehouse/inventory/scan")
async def scan_item(
    data:         ScanRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """
    Procesa un código escaneado:
    - Parsea el contenido (JSON / pipe / ID)
    - Si ya existe un ítem PENDING con el mismo raw_code para esta empresa
      → incrementa quantity y recalcula totales
    - Si no existe → crea un nuevo ítem
    - Si viene batch_id → flujo B: marca status=added_to_batch
    """
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "inventory", "create", company.id)
    parsed  = parse_scanned_code(data.raw_code)

    # ── Buscar duplicado pendiente ────────────────────────────────────────────
    existing = db.execute(
        select(WarehouseInventoryItem).where(
            WarehouseInventoryItem.company_id == company.id,
            WarehouseInventoryItem.raw_code   == data.raw_code,
            WarehouseInventoryItem.status     == InventoryItemStatus.PENDING,
        )
    ).scalar_one_or_none()

    if existing:
        # Misma caja escaneada de nuevo → incrementar cantidad
        existing.quantity        += 1
        existing.peso_lbs_total   = float(existing.peso_lbs_unit)    * existing.quantity
        existing.volumen_ft3_total= float(existing.volumen_ft3_unit) * existing.quantity
        if data.batch_id:
            existing.batch_id = uuid.UUID(data.batch_id)
            existing.status   = InventoryItemStatus.ADDED_TO_BATCH
        db.commit()
        return {**serialize_inventory_item(existing), "action": "incremented"}

    # ── Crear nuevo ítem ──────────────────────────────────────────────────────
    peso_unit = parsed["peso_lbs_unit"]   or 0.0
    vol_unit  = parsed["volumen_ft3_unit"] or 0.0

    item = WarehouseInventoryItem(
        id=uuid.uuid4(),
        company_id=company.id,
        raw_code=data.raw_code,
        codigo_cliente=parsed["codigo_cliente"],
        destino=parsed["destino"],
        direccion=parsed["direccion"],
        lat=parsed["lat"],
        lng=parsed["lng"],
        peso_lbs_unit=peso_unit,
        volumen_ft3_unit=vol_unit,
        peso_lbs_total=peso_unit,
        volumen_ft3_total=vol_unit,
        contacto=parsed["contacto"],
        telefono=parsed["telefono"],
        notas=parsed["notas"],
        quantity=1,
        status=InventoryItemStatus.ADDED_TO_BATCH if data.batch_id else InventoryItemStatus.PENDING,
        batch_id=uuid.UUID(data.batch_id) if data.batch_id else None,
        scanned_by_id=current_user.id,
    )
    db.add(item)
    db.commit()
    return {**serialize_inventory_item(item), "action": "created"}


@app.get("/api/v1/warehouse/inventory")
async def list_inventory(
    status:       Optional[str] = "pending",
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Lista el inventario del día filtrado por estado."""
    company = get_current_company(current_user, db, x_company_id)
    query   = select(WarehouseInventoryItem).where(
        WarehouseInventoryItem.company_id == company.id
    ).order_by(WarehouseInventoryItem.scanned_at.desc())

    if status and status != "all":
        try:
            query = query.where(
                WarehouseInventoryItem.status == InventoryItemStatus(status)
            )
        except ValueError:
            pass

    items = db.execute(query).scalars().all()
    return [serialize_inventory_item(i) for i in items]


@app.patch("/api/v1/warehouse/inventory/{item_id}")
async def update_inventory_item(
    item_id:      str,
    data:         InventoryItemUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Edita los datos de un ítem escaneado (completar info faltante)."""
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "inventory", "edit", company.id)
    item    = db.get(WarehouseInventoryItem, uuid.UUID(item_id))
    if not item or item.company_id != company.id:
        raise HTTPException(status_code=404, detail="Ítem no encontrado")

    if data.destino          is not None: item.destino          = data.destino
    if data.direccion        is not None: item.direccion        = data.direccion
    if data.lat              is not None: item.lat              = data.lat
    if data.lng              is not None: item.lng              = data.lng
    if data.contacto         is not None: item.contacto         = data.contacto
    if data.telefono         is not None: item.telefono         = data.telefono
    if data.notas            is not None: item.notas            = data.notas
    if data.codigo_cliente   is not None: item.codigo_cliente   = data.codigo_cliente
    if data.status           is not None:
        item.status = InventoryItemStatus(data.status)

    if data.peso_lbs_unit    is not None:
        item.peso_lbs_unit    = data.peso_lbs_unit
        item.peso_lbs_total   = data.peso_lbs_unit * item.quantity
    if data.volumen_ft3_unit is not None:
        item.volumen_ft3_unit  = data.volumen_ft3_unit
        item.volumen_ft3_total = data.volumen_ft3_unit * item.quantity

    db.commit()
    return serialize_inventory_item(item)


@app.delete("/api/v1/warehouse/inventory/{item_id}", status_code=204)
async def cancel_inventory_item(
    item_id:      str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Cancela (soft delete) un ítem del inventario."""
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "inventory", "delete", company.id)
    item    = db.get(WarehouseInventoryItem, uuid.UUID(item_id))
    if not item or item.company_id != company.id:
        raise HTTPException(status_code=404, detail="Ítem no encontrado")
    item.status = InventoryItemStatus.CANCELLED
    db.commit()


@app.post("/api/v1/warehouse/inventory/create-batch-stops")
async def inventory_to_batch_stops(
    data:         dict,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """
    Flujo A: convierte ítems de inventario PENDING en paradas para el dispatcher.
    Devuelve las paradas en el mismo formato que StopInput para que el frontend
    las inyecte en batchStops y continúe el flujo normal del batch planner.
    """
    company    = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "inventory", "edit", company.id)
    item_ids   = data.get("item_ids", [])  # lista de UUIDs a convertir, vacío = todos pending

    query = select(WarehouseInventoryItem).where(
        WarehouseInventoryItem.company_id == company.id,
        WarehouseInventoryItem.status     == InventoryItemStatus.PENDING,
    )
    if item_ids:
        query = query.where(WarehouseInventoryItem.id.in_([uuid.UUID(i) for i in item_ids]))

    items = db.execute(query).scalars().all()
    if not items:
        raise HTTPException(status_code=400, detail="No hay ítems pendientes para convertir")

    stops = []
    for item in items:
        stops.append({
            "destino":          item.destino      or item.raw_code,
            "direccion":        item.direccion    or "",
            "lat":              item.lat          or 0.0,
            "lng":              item.lng          or 0.0,
            "peso_lbs":         float(item.peso_lbs_total),
            "volumen_ft3":      float(item.volumen_ft3_total),
            "hora_limite":      "17:00",
            "codigo_cliente":   item.codigo_cliente or "",
            "contacto":         item.contacto    or "",
            "telefono":         item.telefono    or "",
            "codigo_paquete":   item.raw_code,
            "codigo_barras":    item.raw_code,
            "codigo_qr":        "",
            "notas":            item.notas        or "",
            "valor_declarado":  0.0,
            "source":           "scan",
            "quantity":         item.quantity,
            "inventory_item_id": str(item.id),
        })
        # Marcar como added_to_batch temporalmente
        item.status = InventoryItemStatus.ADDED_TO_BATCH

    db.commit()
    return {"stops": stops, "count": len(stops)}


# ─── Recepción de mercancía (inbound) ─────────────────────────────────────────

def next_reception_number(company_id: uuid.UUID, db: Session) -> str:
    """Genera el siguiente número secuencial de recepción: ORL-REC-0001, 0002, ..."""
    last = db.execute(
        select(Reception.reception_number)
        .where(Reception.company_id == company_id, Reception.reception_number.isnot(None))
        .order_by(Reception.reception_number.desc())
        .limit(1)
    ).scalar_one_or_none()

    if last:
        try:
            n = int(last.split('-')[-1]) + 1
        except ValueError:
            n = 1
    else:
        n = 1
    return f"ORL-REC-{n:04d}"


def serialize_reception_item(ri: ReceptionItem) -> dict:
    return {
        "id":                str(ri.id),
        "codigo_cliente":    ri.codigo_cliente,
        "descripcion":       ri.descripcion,
        "expected_quantity": ri.expected_quantity,
        "received_quantity": ri.received_quantity,
        "peso_lbs_unit":     float(ri.peso_lbs_unit),
        "volumen_ft3_unit":  float(ri.volumen_ft3_unit),
        "status":            ri.status.value if hasattr(ri.status, 'value') else ri.status,
        "notas":             ri.notas,
        "inventory_item_id": str(ri.inventory_item_id) if ri.inventory_item_id else None,
    }


def serialize_reception(r: Reception, include_items: bool = True) -> dict:
    items = list(r.items)
    data = {
        "id":               str(r.id),
        "reception_number": r.reception_number,
        "source_type":      r.source_type.value if hasattr(r.source_type, 'value') else r.source_type,
        "reference":        r.reference,
        "origin_route_id":  str(r.origin_route_id) if r.origin_route_id else None,
        "status":           r.status.value if hasattr(r.status, 'value') else r.status,
        "expected_at":      r.expected_at.isoformat() if r.expected_at else None,
        "received_at":      r.received_at.isoformat() if r.received_at else None,
        "notes":            r.notes,
        "created_at":       r.created_at.isoformat() if r.created_at else None,
        "summary": {
            "expected_items": len(items),
            "expected_qty":   sum(i.expected_quantity for i in items),
            "received":       sum(1 for i in items if i.status == ReceptionItemStatus.RECEIVED),
            "damaged":        sum(1 for i in items if i.status == ReceptionItemStatus.DAMAGED),
            "missing":        sum(1 for i in items if i.status == ReceptionItemStatus.MISSING),
            "pending":        sum(1 for i in items if i.status == ReceptionItemStatus.PENDING),
        },
    }
    if include_items:
        data["items"] = [serialize_reception_item(i) for i in items]
    return data


class ReceptionItemInput(BaseModel):
    codigo_cliente:    str   = Field("", max_length=100)
    descripcion:       str   = Field("", max_length=255)
    expected_quantity: int   = Field(1, ge=1)
    peso_lbs_unit:     float = Field(0.0, ge=0)
    volumen_ft3_unit:  float = Field(0.0, ge=0)
    notas:             str   = ""


class CreateReceptionRequest(BaseModel):
    source_type:     str
    reference:       str = Field("", max_length=255)
    origin_route_id: Optional[str] = None
    expected_at:     Optional[str] = None
    notes:           str = ""
    items:           list[ReceptionItemInput] = Field(min_length=1)


class UpdateReceptionRequest(BaseModel):
    reference:   Optional[str] = None
    expected_at: Optional[str] = None
    notes:       Optional[str] = None


class CheckInReceptionItemRequest(BaseModel):
    received_quantity: int = Field(..., ge=0)
    status:             str  # "received" | "damaged"
    notas:              Optional[str] = None


def _get_reception_or_404(db: Session, reception_id: str, company_id: uuid.UUID) -> Reception:
    reception = db.get(Reception, uuid.UUID(reception_id))
    if not reception or reception.company_id != company_id:
        raise HTTPException(status_code=404, detail="Recepción no encontrada")
    return reception


@app.post("/api/v1/warehouse/receptions", status_code=201)
async def create_reception(
    data:         CreateReceptionRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Crea una recepción esperada (manifiesto) — todavía no implica que algo llegó físicamente."""
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "receptions", "create", company.id)

    try:
        source_type = ReceptionSourceType(data.source_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="source_type debe ser 'supplier' o 'return'")

    origin_route_uuid = None
    if data.origin_route_id:
        origin_route_uuid = uuid.UUID(data.origin_route_id)
        route = db.get(RouteHeader, origin_route_uuid)
        if not route:
            raise HTTPException(status_code=404, detail="La ruta de origen indicada no existe")

    reception = Reception(
        id=uuid.uuid4(),
        company_id=company.id,
        reception_number=next_reception_number(company.id, db),
        source_type=source_type,
        reference=data.reference or None,
        origin_route_id=origin_route_uuid,
        status=ReceptionStatus.EXPECTED,
        expected_at=datetime.fromisoformat(data.expected_at) if data.expected_at else None,
        notes=data.notes or None,
        created_by_id=current_user.id,
    )
    db.add(reception)
    db.flush()

    for it in data.items:
        db.add(ReceptionItem(
            id=uuid.uuid4(),
            reception_id=reception.id,
            codigo_cliente=it.codigo_cliente or None,
            descripcion=it.descripcion or None,
            expected_quantity=it.expected_quantity,
            peso_lbs_unit=it.peso_lbs_unit,
            volumen_ft3_unit=it.volumen_ft3_unit,
            notas=it.notas or None,
            status=ReceptionItemStatus.PENDING,
        ))

    db.commit()
    db.refresh(reception)
    return serialize_reception(reception)


@app.get("/api/v1/warehouse/receptions")
async def list_receptions(
    status:       Optional[str] = None,
    source_type:  Optional[str] = None,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    company = get_current_company(current_user, db, x_company_id)
    query = select(Reception).where(Reception.company_id == company.id).order_by(Reception.created_at.desc())

    if status:
        try:
            query = query.where(Reception.status == ReceptionStatus(status))
        except ValueError:
            pass
    if source_type:
        try:
            query = query.where(Reception.source_type == ReceptionSourceType(source_type))
        except ValueError:
            pass

    receptions = db.execute(query).scalars().all()
    return [serialize_reception(r, include_items=False) for r in receptions]


@app.get("/api/v1/warehouse/receptions/{reception_id}")
async def get_reception(
    reception_id: str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    company   = get_current_company(current_user, db, x_company_id)
    reception = _get_reception_or_404(db, reception_id, company.id)
    return serialize_reception(reception)


@app.patch("/api/v1/warehouse/receptions/{reception_id}")
async def update_reception(
    reception_id: str,
    data:         UpdateReceptionRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    company   = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "receptions", "edit", company.id)
    reception = _get_reception_or_404(db, reception_id, company.id)
    if reception.status == ReceptionStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="La recepción ya está cerrada, no se puede editar")

    if data.reference   is not None: reception.reference   = data.reference or None
    if data.notes       is not None: reception.notes       = data.notes or None
    if data.expected_at is not None:
        reception.expected_at = datetime.fromisoformat(data.expected_at) if data.expected_at else None

    db.commit()
    db.refresh(reception)
    return serialize_reception(reception)


@app.post("/api/v1/warehouse/receptions/{reception_id}/items", status_code=201)
async def add_reception_item(
    reception_id: str,
    data:         ReceptionItemInput,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    company   = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "receptions", "create", company.id)
    reception = _get_reception_or_404(db, reception_id, company.id)
    if reception.status == ReceptionStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="La recepción ya está cerrada, no se pueden agregar ítems")

    item = ReceptionItem(
        id=uuid.uuid4(),
        reception_id=reception.id,
        codigo_cliente=data.codigo_cliente or None,
        descripcion=data.descripcion or None,
        expected_quantity=data.expected_quantity,
        peso_lbs_unit=data.peso_lbs_unit,
        volumen_ft3_unit=data.volumen_ft3_unit,
        notas=data.notas or None,
        status=ReceptionItemStatus.PENDING,
    )
    db.add(item)
    db.commit()
    return serialize_reception_item(item)


@app.delete("/api/v1/warehouse/receptions/{reception_id}/items/{item_id}", status_code=204)
async def delete_reception_item(
    reception_id: str,
    item_id:      str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    company   = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "receptions", "delete", company.id)
    reception = _get_reception_or_404(db, reception_id, company.id)
    if reception.status == ReceptionStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="La recepción ya está cerrada")

    item = db.get(ReceptionItem, uuid.UUID(item_id))
    if not item or item.reception_id != reception.id:
        raise HTTPException(status_code=404, detail="Ítem no encontrado")
    if item.status != ReceptionItemStatus.PENDING:
        raise HTTPException(status_code=400, detail="No se puede borrar un ítem que ya tuvo check-in")

    db.delete(item)
    db.commit()


@app.patch("/api/v1/warehouse/receptions/{reception_id}/items/{item_id}/check-in")
async def check_in_reception_item(
    reception_id: str,
    item_id:      str,
    data:         CheckInReceptionItemRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """
    Marca el resultado físico del check-in de un ítem esperado:
    - status='received' → se crea (o actualiza) el WarehouseInventoryItem
      correspondiente, disponible (pending) para armar lotes de salida.
    - status='damaged'  → queda registrado en la recepción, pero NO genera
      inventario disponible (no se puede despachar mercancía dañada).
    """
    company   = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "receptions", "edit", company.id)
    reception = _get_reception_or_404(db, reception_id, company.id)
    if reception.status == ReceptionStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="La recepción ya está cerrada")

    item = db.get(ReceptionItem, uuid.UUID(item_id))
    if not item or item.reception_id != reception.id:
        raise HTTPException(status_code=404, detail="Ítem no encontrado")

    try:
        new_status = ReceptionItemStatus(data.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="status debe ser 'received' o 'damaged'")
    if new_status not in (ReceptionItemStatus.RECEIVED, ReceptionItemStatus.DAMAGED):
        raise HTTPException(status_code=400, detail="status debe ser 'received' o 'damaged'")

    item.received_quantity = data.received_quantity
    item.status = new_status
    if data.notas is not None:
        item.notas = data.notas or None

    if new_status == ReceptionItemStatus.RECEIVED and data.received_quantity > 0:
        peso_unit = float(item.peso_lbs_unit)
        vol_unit  = float(item.volumen_ft3_unit)
        if item.inventory_item_id:
            inv = db.get(WarehouseInventoryItem, item.inventory_item_id)
            inv.quantity           = data.received_quantity
            inv.peso_lbs_total     = peso_unit * data.received_quantity
            inv.volumen_ft3_total  = vol_unit  * data.received_quantity
        else:
            inv = WarehouseInventoryItem(
                id=uuid.uuid4(),
                company_id=company.id,
                raw_code=item.codigo_cliente or f"REC-{reception.reception_number}-{str(item.id)[:8]}",
                codigo_cliente=item.codigo_cliente,
                peso_lbs_unit=peso_unit,
                volumen_ft3_unit=vol_unit,
                notas=f"Recepción {reception.reception_number}",
                quantity=data.received_quantity,
                peso_lbs_total=peso_unit * data.received_quantity,
                volumen_ft3_total=vol_unit * data.received_quantity,
                status=InventoryItemStatus.PENDING,
                scanned_by_id=current_user.id,
            )
            db.add(inv)
            db.flush()
            item.inventory_item_id = inv.id

    if reception.status == ReceptionStatus.EXPECTED:
        reception.status = ReceptionStatus.IN_PROGRESS

    db.commit()
    return serialize_reception_item(item)


@app.post("/api/v1/warehouse/receptions/{reception_id}/complete")
async def complete_reception(
    reception_id: str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Cierra la recepción: cualquier ítem que siga 'pending' pasa a 'missing'."""
    company   = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "receptions", "approve", company.id)
    reception = _get_reception_or_404(db, reception_id, company.id)
    if reception.status == ReceptionStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="La recepción ya está cerrada")

    for item in reception.items:
        if item.status == ReceptionItemStatus.PENDING:
            item.status = ReceptionItemStatus.MISSING

    reception.status      = ReceptionStatus.COMPLETED
    reception.received_at = datetime.utcnow()
    db.commit()
    db.refresh(reception)
    return serialize_reception(reception)


# ─── Catálogo de mensajes ──────────────────────────────────────────────────────

_TEMPLATE_VAR_RE = re.compile(r'\{(\w+)\}')


def extract_template_variables(macro_text: str) -> list[str]:
    """Devuelve, ordenados, los nombres de variable {var} detectados en la plantilla."""
    return sorted(set(_TEMPLATE_VAR_RE.findall(macro_text or "")))


def render_message_template(macro_text: str, context: dict) -> str:
    """
    Sustituye variables {nombre} de la plantilla con valores de `context`.
    Una variable sin valor provisto se deja intacta (no rompe el render si
    el llamador todavía no tiene todos los datos) en vez de lanzar KeyError.
    """
    def _sub(match: "re.Match") -> str:
        key = match.group(1)
        return str(context[key]) if key in context else match.group(0)
    return _TEMPLATE_VAR_RE.sub(_sub, macro_text or "")


def serialize_message_template(t: MessageTemplate) -> dict:
    return {
        "id":          str(t.id),
        "code":        t.code,
        "description": t.description,
        "macro_text":  t.macro_text,
        "variables":   extract_template_variables(t.macro_text),
        "is_active":   t.is_active,
        "created_at":  t.created_at.isoformat() if t.created_at else None,
        "updated_at":  t.updated_at.isoformat() if t.updated_at else None,
    }


class MessageTemplateCreate(BaseModel):
    code:        str  = Field(min_length=1, max_length=60)
    description: str  = Field(min_length=1, max_length=255)
    macro_text:  str  = Field(min_length=1)
    is_active:   bool = True


class MessageTemplateUpdate(BaseModel):
    description: Optional[str]  = Field(None, min_length=1, max_length=255)
    macro_text:  Optional[str]  = Field(None, min_length=1)
    is_active:   Optional[bool] = None


class RenderMessageRequest(BaseModel):
    context: dict = {}


@app.get("/api/v1/messages")
async def list_message_templates(
    all:          bool = False,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Catálogo de mensajes — solo activos por default, all=true incluye inactivos."""
    query = select(MessageTemplate)
    if not all:
        query = query.where(MessageTemplate.is_active == True)
    templates = db.execute(query.order_by(MessageTemplate.code)).scalars().all()
    return [serialize_message_template(t) for t in templates]


@app.post("/api/v1/admin/messages", status_code=201)
async def create_message_template(
    data:         MessageTemplateCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Crea un mensaje estándar en el catálogo."""
    require_permission(current_user, db, "messages", "create")
    existing = db.execute(
        select(MessageTemplate).where(MessageTemplate.code == data.code)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"Ya existe un mensaje con código '{data.code}'")

    t = MessageTemplate(
        id=uuid.uuid4(), code=data.code, description=data.description,
        macro_text=data.macro_text, is_active=data.is_active,
    )
    db.add(t)
    db.commit()
    return serialize_message_template(t)


@app.patch("/api/v1/admin/messages/{message_id}")
async def update_message_template(
    message_id:   str,
    data:         MessageTemplateUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Actualiza un mensaje del catálogo (el código no se puede cambiar)."""
    require_permission(current_user, db, "messages", "edit")
    t = db.get(MessageTemplate, uuid.UUID(message_id))
    if not t:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado")

    if data.description is not None: t.description = data.description
    if data.macro_text  is not None: t.macro_text  = data.macro_text
    if data.is_active   is not None: t.is_active   = data.is_active

    db.commit()
    return serialize_message_template(t)


@app.post("/api/v1/messages/{message_id}/render")
async def render_message_template_endpoint(
    message_id:   str,
    data:         RenderMessageRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Devuelve el texto de un mensaje con sus variables sustituidas por `context`."""
    t = db.get(MessageTemplate, uuid.UUID(message_id))
    if not t:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado")
    rendered = render_message_template(t.macro_text, data.context)
    missing  = [v for v in extract_template_variables(t.macro_text) if v not in data.context]
    return {"rendered": rendered, "missing_variables": missing}


# ─── Negociación de precios por ruta ──────────────────────────────────────────

def calculate_suggested_price(route: RouteHeader, svc: "ServiceType") -> float:
    """
    Sugiere el precio inicial de una ruta en base a distancia + peso + volumen:
        precio = precio_servicio (base del tipo de servicio)
               + precio_por_km  * total_km_estimated
               + precio_por_lb  * total_weight_lbs
               + precio_por_ft3 * total_volume_ft3
    Acotado a [importe_minimo, importe_maximo] del tipo de servicio.

    No incorpora demanda todavía — hoy no existe ninguna métrica de demanda
    (disponibilidad de transportistas, histórico de aceptación, etc.) en el
    sistema. Cuando exista, se suma acá como un factor multiplicativo más.
    """
    base = float(svc.precio_servicio or 0)
    km   = float(route.total_km_estimated or 0)
    lbs  = float(route.total_weight_lbs or 0)
    ft3  = float(route.total_volume_ft3 or 0)

    price = (
        base
        + float(svc.precio_por_km or 0)  * km
        + float(svc.precio_por_lb or 0)  * lbs
        + float(svc.precio_por_ft3 or 0) * ft3
    )

    price = max(price, float(svc.importe_minimo or 0))
    if svc.importe_maximo is not None:
        price = min(price, float(svc.importe_maximo))
    return round(price, 2)


def _apply_formula_price(route: RouteHeader, svc: Optional["ServiceType"]) -> None:
    """
    Fija el precio inicial de una ruta recién creada con la fórmula de
    calculate_suggested_price(), para que gross_pay no arranque en $0 —
    a partir de ahí el warehouse puede dejarlo así o abrir la negociación
    (suggest-price) para intentar conseguir una oferta mejor de algún
    transportista. No toca negotiation_status (queda 'none': todavía no
    se negoció nada, esto es solo el precio de partida).

    Requiere que route.total_weight_lbs/total_volume_ft3 ya reflejen los
    triggers de Postgres (hacer db.refresh(route) después de crear sus
    paradas/ítems y antes de llamar a esta función).
    """
    if not svc:
        return
    price = calculate_suggested_price(route, svc)
    route.gross_pay            = price
    route.muevo_commission_pct = 5.00
    route.muevo_commission_amt = round(price * 0.05, 2)
    route.net_pay_estimated    = round(price * 0.95, 2)


def _open_route_marketplace(route: RouteHeader, current_user: User, db: Session) -> None:
    """
    Abre el marketplace de negociación de una ruta usando su precio actual
    (gross_pay, ya fijado por _apply_formula_price al crearla) como ask
    público inicial — mismo efecto que suggest-price, pero disparado
    automáticamente al aprobar el lote en vez de exigir un paso manual
    aparte. No hace nada si la ruta ya tiene una negociación en curso o
    cerrada (no pisa un suggest-price manual hecho antes de aprobar).
    """
    if route.negotiation_status != NegotiationStatus.NONE:
        return
    amount = float(route.gross_pay or 0)
    if amount <= 0:
        return
    route.suggested_price    = amount
    route.negotiation_status = NegotiationStatus.SUGGESTED
    db.add(RoutePriceOffer(
        id=uuid.uuid4(), route_header_id=route.id, transport_company_id=None,
        offered_by=OfferSource.WAREHOUSE, offered_by_user_id=current_user.id,
        amount=amount, note="Precio inicial al aprobar el lote", status=OfferStatus.PENDING,
    ))


def serialize_offer(o: RoutePriceOffer, db: Session) -> dict:
    user = db.get(User, o.offered_by_user_id) if o.offered_by_user_id else None
    tc   = db.get(TransportCompany, o.transport_company_id) if o.transport_company_id else None
    return {
        "id":                     str(o.id),
        "route_id":               str(o.route_header_id),
        "transport_company_id":   str(o.transport_company_id) if o.transport_company_id else None,
        "transport_company_name": tc.name if tc else None,
        "offered_by":             o.offered_by.value if hasattr(o.offered_by,'value') else o.offered_by,
        "user_name":              user.full_name if user else None,
        "amount":                 float(o.amount),
        "note":                   o.note,
        "status":                 o.status.value if hasattr(o.status,'value') else o.status,
        "created_at":             o.created_at.isoformat() if o.created_at else None,
    }


def serialize_negotiation(route: RouteHeader, db: Session) -> dict:
    """
    Devuelve el estado completo de la negociación: el historial del precio
    público (broadcast) del warehouse, y un hilo independiente por cada
    empresa de transporte que haya pujado (marketplace abierto — cualquier
    transportista activo puede tener su propio hilo, sin pisar a los demás).
    """
    offers = db.execute(
        select(RoutePriceOffer)
        .where(RoutePriceOffer.route_header_id == route.id)
        .order_by(RoutePriceOffer.created_at.asc())
    ).scalars().all()

    broadcast_offers = [o for o in offers if o.transport_company_id is None]

    threads_order: List[uuid.UUID] = []
    threads_offers: dict = {}
    for o in offers:
        if o.transport_company_id is None:
            continue
        if o.transport_company_id not in threads_offers:
            threads_order.append(o.transport_company_id)
            threads_offers[o.transport_company_id] = []
        threads_offers[o.transport_company_id].append(o)

    threads = []
    for tc_id in threads_order:
        tc = db.get(TransportCompany, tc_id)
        routes_with_us = db.execute(
            select(func.count(RouteHeader.id)).where(
                RouteHeader.company_id == route.company_id,
                RouteHeader.transport_company_id == tc_id,
            )
        ).scalar()
        threads.append({
            "transport_company_id":   str(tc_id),
            "transport_company_name": tc.name if tc else None,
            "avg_rating":             float(tc.avg_rating or 0) if tc else None,
            "on_time_pct":            float(tc.on_time_pct or 0) if tc else None,
            # Info adicional para que el warehouse pueda evaluar al
            # transportista antes de elegir su oferta — no solo el monto.
            "is_verified":            tc.is_verified if tc else False,
            "total_routes":           tc.total_routes if tc else 0,
            "completed_routes":       tc.completed_routes if tc else 0,
            "rejected_routes":        tc.rejected_routes if tc else 0,
            "routes_with_us":         routes_with_us or 0,
            "contact_name":           tc.contact_name if tc else None,
            "contact_phone":          tc.contact_phone if tc else None,
            "city":                   tc.city if tc else None,
            "state":                  tc.state if tc else None,
            "dot_number":             tc.dot_number if tc else None,
            "mc_number":              tc.mc_number if tc else None,
            "offers":                 [serialize_offer(o, db) for o in threads_offers[tc_id]],
        })

    svc = db.get(ServiceType, route.service_type_id) if route.service_type_id else None
    system_suggested_price = calculate_suggested_price(route, svc) if svc else None

    pending_holding_approval = None
    if route.pending_offer_id:
        po = db.get(RoutePriceOffer, route.pending_offer_id)
        if po:
            tc = db.get(TransportCompany, po.transport_company_id) if po.transport_company_id else None
            pending_holding_approval = {
                "offer_id":               str(po.id),
                "transport_company_id":   str(po.transport_company_id) if po.transport_company_id else None,
                "transport_company_name": tc.name if tc else None,
                "amount":                 float(po.amount),
            }

    return {
        "route_id":                  str(route.id),
        "route_number":              route.route_number,
        "negotiation_status":        route.negotiation_status.value if hasattr(route.negotiation_status,'value') else route.negotiation_status,
        "suggested_price":           float(route.suggested_price) if route.suggested_price else None,
        "system_suggested_price":    system_suggested_price,
        "current_price":             float(route.gross_pay or 0),
        "broadcast_offers":          [serialize_offer(o, db) for o in broadcast_offers],
        "threads":                   threads,
        "pending_holding_approval":  pending_holding_approval,
    }


class SuggestPriceRequest(BaseModel):
    amount: float
    note:   Optional[str] = None


class CounterOfferRequest(BaseModel):
    amount: float
    note:   Optional[str] = None


def _close_negotiation(route: RouteHeader, final_amount: float, db: Session):
    """Cierra la negociación: fija el precio final y aplica la comisión Muevo del 5%."""
    route.gross_pay             = final_amount
    route.muevo_commission_pct  = 5.00
    route.muevo_commission_amt  = round(final_amount * 0.05, 2)
    route.net_pay_estimated     = round(final_amount * 0.95, 2)
    route.negotiation_status    = NegotiationStatus.ACCEPTED

    # Marcar todas las ofertas pendientes como superseded excepto la ganadora
    pending = db.execute(
        select(RoutePriceOffer).where(
            RoutePriceOffer.route_header_id == route.id,
            RoutePriceOffer.status == OfferStatus.PENDING,
        )
    ).scalars().all()
    for p in pending:
        p.status = OfferStatus.SUPERSEDED


@app.get("/api/v1/warehouse/routes/{route_id}/negotiation")
async def get_route_negotiation(
    route_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Devuelve el historial completo de ofertas de una ruta."""
    company = get_current_company(current_user, db, x_company_id)
    route   = db.get(RouteHeader, uuid.UUID(route_id))
    if not route or route.company_id != company.id:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    return serialize_negotiation(route, db)


@app.post("/api/v1/warehouse/routes/{route_id}/suggest-price")
async def suggest_price(
    route_id:     str,
    data:         SuggestPriceRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """
    El warehouse sugiere (o actualiza) el precio público de la ruta, abriendo
    el marketplace a pujas de cualquier empresa de transporte activa. Puede
    volver a llamarse mientras el marketplace esté abierto para ajustar el
    ask — la oferta pública anterior queda en el historial como superseded.
    """
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "pricing", "create", company.id)
    route   = db.get(RouteHeader, uuid.UUID(route_id))
    if not route or route.company_id != company.id:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    if route.negotiation_status in (NegotiationStatus.PENDING_HOLDING_APPROVAL, NegotiationStatus.ACCEPTED):
        raise HTTPException(status_code=400, detail="No se puede modificar el precio: ya hay una oferta seleccionada")

    prev_broadcast = db.execute(
        select(RoutePriceOffer).where(
            RoutePriceOffer.route_header_id == route.id,
            RoutePriceOffer.transport_company_id.is_(None),
            RoutePriceOffer.status == OfferStatus.PENDING,
        )
    ).scalars().all()
    for p in prev_broadcast:
        p.status = OfferStatus.SUPERSEDED

    route.suggested_price    = data.amount
    route.negotiation_status = NegotiationStatus.SUGGESTED

    offer = RoutePriceOffer(
        id=uuid.uuid4(), route_header_id=route.id, transport_company_id=None,
        offered_by=OfferSource.WAREHOUSE, offered_by_user_id=current_user.id,
        amount=data.amount, note=data.note, status=OfferStatus.PENDING,
    )
    db.add(offer)
    db.commit()
    return serialize_negotiation(route, db)


def _require_open_marketplace_route(route: Optional[RouteHeader]) -> None:
    if not route:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    if route.negotiation_status != NegotiationStatus.SUGGESTED:
        raise HTTPException(status_code=400, detail="Esta ruta no está abierta a pujas en este momento")


@app.get("/api/v1/transport/marketplace")
async def transport_marketplace(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Rutas de cualquier company abiertas a pujas (marketplace abierto). Incluye
    el ask público actual y, si esta empresa ya pujó, su propio hilo — nunca
    el de otras empresas.
    """
    tc = get_current_transport_company(current_user, db)
    routes = db.execute(
        select(RouteHeader).where(RouteHeader.negotiation_status == NegotiationStatus.SUGGESTED)
        .order_by(RouteHeader.created_at.desc())
    ).scalars().all()

    result = []
    for route in routes:
        negotiation = serialize_negotiation(route, db)
        my_thread = next(
            (t for t in negotiation["threads"] if t["transport_company_id"] == str(tc.id)), None
        )
        result.append({
            "route_id":       str(route.id),
            "route_number":   route.route_number,
            "title":          route.title,
            "service_mode":   route.service_mode.value if hasattr(route.service_mode,'value') else route.service_mode,
            "scheduled_date": route.scheduled_date.isoformat() if route.scheduled_date else None,
            "total_stops":    route.total_stops,
            "total_weight_lbs": float(route.total_weight_lbs or 0),
            "total_volume_ft3": float(route.total_volume_ft3 or 0),
            "current_ask":    float(route.suggested_price) if route.suggested_price else None,
            "my_thread":      my_thread,
        })
    return result


@app.get("/api/v1/transport/routes/{route_id}/negotiation")
async def transport_route_negotiation(
    route_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Detalle de una ruta en negociación para esta empresa: el ask público + solo el hilo propio."""
    tc    = get_current_transport_company(current_user, db)
    route = db.get(RouteHeader, uuid.UUID(route_id))
    if not route:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    negotiation = serialize_negotiation(route, db)
    negotiation["threads"] = [t for t in negotiation["threads"] if t["transport_company_id"] == str(tc.id)]
    return negotiation


@app.post("/api/v1/transport/routes/{route_id}/counter-offer")
async def transport_counter_offer(
    route_id:     str,
    data:         CounterOfferRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """La empresa de transporte hace/actualiza su propia contraoferta — no afecta los hilos de otras empresas."""
    tc    = get_current_transport_company(current_user, db)
    route = db.get(RouteHeader, uuid.UUID(route_id))
    _require_open_marketplace_route(route)

    prev = db.execute(
        select(RoutePriceOffer).where(
            RoutePriceOffer.route_header_id == route.id,
            RoutePriceOffer.transport_company_id == tc.id,
            RoutePriceOffer.status == OfferStatus.PENDING,
        )
    ).scalars().all()
    for p in prev:
        p.status = OfferStatus.SUPERSEDED

    offer = RoutePriceOffer(
        id=uuid.uuid4(), route_header_id=route.id, transport_company_id=tc.id,
        offered_by=OfferSource.TRANSPORT, offered_by_user_id=current_user.id,
        amount=data.amount, note=data.note, status=OfferStatus.PENDING,
    )
    db.add(offer)
    db.commit()
    return serialize_negotiation(route, db)


@app.post("/api/v1/transport/routes/{route_id}/accept-price")
async def transport_accept_price(
    route_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    La empresa de transporte puja al precio público actual del warehouse.
    No cierra la negociación — queda como su oferta pendiente en su propio
    hilo hasta que el warehouse elija un ganador (y luego holding lo apruebe).
    """
    tc    = get_current_transport_company(current_user, db)
    route = db.get(RouteHeader, uuid.UUID(route_id))
    _require_open_marketplace_route(route)

    current_ask = route.suggested_price
    if not current_ask:
        raise HTTPException(status_code=400, detail="No hay un precio público para aceptar")

    prev = db.execute(
        select(RoutePriceOffer).where(
            RoutePriceOffer.route_header_id == route.id,
            RoutePriceOffer.transport_company_id == tc.id,
            RoutePriceOffer.status == OfferStatus.PENDING,
        )
    ).scalars().all()
    for p in prev:
        p.status = OfferStatus.SUPERSEDED

    offer = RoutePriceOffer(
        id=uuid.uuid4(), route_header_id=route.id, transport_company_id=tc.id,
        offered_by=OfferSource.TRANSPORT, offered_by_user_id=current_user.id,
        amount=current_ask, note="Aceptó el precio público", status=OfferStatus.PENDING,
    )
    db.add(offer)
    db.commit()
    return serialize_negotiation(route, db)


@app.post("/api/v1/warehouse/routes/{route_id}/select-offer/{transport_company_id}")
async def warehouse_select_offer(
    route_id:             str,
    transport_company_id: str,
    current_user:         User    = Depends(get_current_user),
    db:                   Session = Depends(get_db),
    x_company_id:         Optional[str] = Header(None),
):
    """
    El warehouse elige la oferta ganadora de una empresa de transporte entre
    todas las que pujaron. Esto NO cierra el trato: la ruta pasa a estar
    "en holding" hasta que alguien con permiso a nivel holding la apruebe
    (ver /holding-approve). Las ofertas pendientes de las demás empresas
    quedan fuera de juego (rejected).
    """
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "pricing", "approve", company.id)
    route   = db.get(RouteHeader, uuid.UUID(route_id))
    if not route or route.company_id != company.id:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    if route.negotiation_status != NegotiationStatus.SUGGESTED:
        raise HTTPException(status_code=400, detail="La negociación no está abierta a selección en este momento")

    tc_uuid = uuid.UUID(transport_company_id)
    winning_offer = db.execute(
        select(RoutePriceOffer).where(
            RoutePriceOffer.route_header_id == route.id,
            RoutePriceOffer.transport_company_id == tc_uuid,
            RoutePriceOffer.status == OfferStatus.PENDING,
        ).order_by(RoutePriceOffer.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if not winning_offer:
        raise HTTPException(status_code=400, detail="Esa empresa de transporte no tiene una oferta pendiente en esta ruta")

    other_pending = db.execute(
        select(RoutePriceOffer).where(
            RoutePriceOffer.route_header_id == route.id,
            RoutePriceOffer.transport_company_id.isnot(None),
            RoutePriceOffer.transport_company_id != tc_uuid,
            RoutePriceOffer.status == OfferStatus.PENDING,
        )
    ).scalars().all()
    for o in other_pending:
        o.status = OfferStatus.REJECTED

    winning_offer.status        = OfferStatus.SELECTED
    route.pending_offer_id      = winning_offer.id
    route.negotiation_status    = NegotiationStatus.PENDING_HOLDING_APPROVAL

    db.commit()
    return serialize_negotiation(route, db)


@app.post("/api/v1/warehouse/routes/{route_id}/holding-approve")
async def holding_approve_negotiation(
    route_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """
    Aprobación final a nivel holding (alcance global — company_id=None en
    require_permission): cierra la negociación con el precio de la oferta
    seleccionada y recién ahí asigna la ruta al transportista ganador.
    """
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "pricing", "approve", company_id=None)
    route   = db.get(RouteHeader, uuid.UUID(route_id))
    if not route or route.company_id != company.id:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    if route.negotiation_status != NegotiationStatus.PENDING_HOLDING_APPROVAL or not route.pending_offer_id:
        raise HTTPException(status_code=400, detail="Esta ruta no tiene una selección pendiente de aprobación de holding")

    winning_offer = db.get(RoutePriceOffer, route.pending_offer_id)
    if not winning_offer:
        raise HTTPException(status_code=400, detail="La oferta seleccionada ya no existe")

    _close_negotiation(route, float(winning_offer.amount), db)
    winning_offer.status       = OfferStatus.ACCEPTED
    route.transport_company_id = winning_offer.transport_company_id
    route.holding_approved_by  = current_user.id
    route.holding_approved_at  = datetime.utcnow()
    route.pending_offer_id     = None
    if route.status == RouteStatus.DRAFT:
        # Recién ahora la ruta tiene transportista y precio final — pasa a
        # publicada para que aparezca en "Rutas ofrecidas" del transportista
        # ganador y pueda aceptarla/asignarle un vehículo (mismo flujo que
        # offer_batch()).
        route.status = RouteStatus.PUBLISHED

    db.commit()
    return serialize_negotiation(route, db)


# ─── Dispatcher — agrupamiento automático de paradas ─────────────────────────

HORA_PATTERN = r'^([01]\d|2[0-3]):[0-5]\d$'  # HH:MM, 00:00–23:59


class StopItemInput(BaseModel):
    """Un paquete individual dentro de una parada."""
    package_code:   str   = ""
    barcode:        str   = ""
    qr_code:        str   = ""
    weight_lbs:     float = Field(0, ge=0)
    volume_ft3:     float = Field(0, ge=0)
    declared_value: float = Field(0, ge=0)


class StopInput(BaseModel):
    """Una parada individual ingresada via CSV o formulario manual."""
    destino:         str   = Field(min_length=1)
    direccion:       str   = Field(min_length=1)
    codigo_postal:   str   = ""
    lat:             float = Field(ge=-90, le=90)
    lng:             float = Field(ge=-180, le=180)
    peso_lbs:        float = Field(ge=0)
    volumen_ft3:     float = Field(ge=0)
    hora_limite:     str   = Field(default="23:59", pattern=HORA_PATTERN)
    contacto:        str   = ""
    telefono:        str   = ""
    codigo_cliente:  str   = ""
    codigo_paquete:  str   = ""
    codigo_barras:   str   = ""
    codigo_qr:       str   = ""
    notas:           str   = ""
    valor_declarado: float = Field(0.0, ge=0)
    source:          str   = "csv"    # "csv" | "manual"
    items:           list[StopItemInput] = []  # desglose de paquetes — vacío = un solo paquete (legacy)


class DispatchPlanRequest(BaseModel):
    """Request para planificar el agrupamiento de paradas."""
    vehicle_type:        str            # furgoneta | furgon | sedan | moto
    service_type:        str = "mensajeria"  # mensajeria | logistica | paqueteria | personas
    peso_max_lbs:        float
    volumen_max_ft3:     float
    max_stops_per_route: int   = 12
    paradas:             list[StopInput] = Field(min_length=1)
    # Sin max_length estático — el tope de filas es configurable por compañía
    # (Company.max_csv_rows) y se valida dentro de plan_batch().


class DispatchConfirmRequest(BaseModel):
    """Confirmar los grupos (posiblemente ajustados) y crear el lote en DB."""
    nombre:          str
    fecha:           str               # ISO date string YYYY-MM-DD
    vehicle_type:    str
    service_type:    str = "mensajeria"  # mensajeria | logistica | paqueteria | personas
    scheduled_start: str               # HH:MM — hora de salida estimada
    scheduled_end:   str               # HH:MM — hora de regreso estimada
    grupos:          list[dict]        # lista de planned_route_to_dict (con stops)
    contract_id:     Optional[str] = None
    client_id:       Optional[str] = None



@app.get("/api/v1/warehouse/batches")
async def warehouse_list_batches(
    status:       Optional[str] = None,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Lista los lotes del warehouse con sus rutas y métricas agregadas."""
    company = get_current_company(current_user, db, x_company_id)

    query = select(RouteBatch).where(RouteBatch.company_id == company.id)
    if status and status != "todos":
        try:
            query = query.where(RouteBatch.status == BatchStatus(status))
        except ValueError:
            pass

    batches = db.execute(query.order_by(RouteBatch.created_at.desc())).scalars().all()

    # Detectar rutas huérfanas (sin lote) — no deberían existir pero por seguridad
    all_route_ids_in_batches = set()
    for b in batches:
        items_check = db.execute(
            select(RouteBatchItem.route_header_id)
            .where(RouteBatchItem.batch_id == b.id)
        ).scalars().all()
        all_route_ids_in_batches.update(items_check)

    orphan_routes = db.execute(
        select(RouteHeader).where(
            RouteHeader.company_id == company.id,
            RouteHeader.id.notin_(all_route_ids_in_batches),
        )
    ).scalars().all()

    # Crear lotes virtuales para rutas huérfanas y persistirlos
    for orphan in orphan_routes:
        auto_batch = RouteBatch(
            id=uuid.uuid4(), company_id=company.id,
            batch_number=next_batch_number(company.id, db),
            status=BatchStatus.DRAFT, notes=orphan.title,
        )
        db.add(auto_batch); db.flush()
        db.add(RouteBatchItem(
            id=uuid.uuid4(), batch_id=auto_batch.id,
            route_header_id=orphan.id, status=BatchItemStatus.PENDING,
        ))
        batches.append(auto_batch)

    if orphan_routes:
        db.commit()

    result = []
    for b in batches:
        # Obtener rutas del lote via batch_items
        items = db.execute(
            select(RouteBatchItem).where(RouteBatchItem.batch_id == b.id)
        ).scalars().all()

        routes = []
        for item in items:
            r = db.get(RouteHeader, item.route_header_id)
            if r:
                routes.append({
                    "id":              str(r.id),
                    "route_number":    r.route_number,
                    "title":           r.title,
                    "status":          r.status.value if hasattr(r.status,'value') else r.status,
                    "total_stops":     r.total_stops or 0,
                    "completed_stops": r.completed_stops or 0,
                    "gross_pay":       float(r.gross_pay or 0),
                    "scheduled_date":  r.scheduled_date.isoformat() if r.scheduled_date else None,
                    "scheduled_start": r.scheduled_start.isoformat() if r.scheduled_start else None,
                    "scheduled_end":   r.scheduled_end.isoformat() if r.scheduled_end else None,
                    "service_mode":    r.service_mode.value if hasattr(r.service_mode,'value') else r.service_mode,
                    "transport_company": r.transport_company.name if r.transport_company else None,
                    "item_status":     item.status.value if hasattr(item.status,'value') else item.status,
                    "vehicle_type":    r.required_vehicle_type,
                    "required_vehicle_type": r.required_vehicle_type,
                    "service_type_code": r.service_type_rel.code if r.service_type_rel else (r.service_mode.value if hasattr(r.service_mode,'value') else r.service_mode),
                    "total_weight_lbs": float(r.total_weight_lbs or 0),
                    "total_volume_ft3": float(r.total_volume_ft3 or 0),
                    "negotiation_status": r.negotiation_status.value if hasattr(r.negotiation_status,'value') else r.negotiation_status,
                    "suggested_price":  float(r.suggested_price) if r.suggested_price else None,
                })

        total_stops     = sum(r["total_stops"]     for r in routes)
        completed_stops = sum(r["completed_stops"] for r in routes)
        pending_routes  = sum(1 for r in routes if r["status"] not in ("completed","cancelled","closed_with_incidents"))
        completed_routes= sum(1 for r in routes if r["status"] == "completed")
        total_cost      = sum(r["gross_pay"]       for r in routes)

        # Fechas del lote: tomar de la primera y última ruta
        dates = [r["scheduled_date"] for r in routes if r["scheduled_date"]]
        fecha_creacion = b.created_at.strftime("%Y-%m-%d") if b.created_at else (dates[0][:10] if dates else "—")
        fecha_estado   = b.updated_at.strftime("%Y-%m-%d") if b.updated_at else fecha_creacion

        result.append({
            "id":               str(b.id),
            "numero":           b.batch_number or (b.notes or str(b.id)[:8].upper()),
            "nombre":           b.notes or "Sin nombre",
            "client_code":      b.client_code,
            "status":           b.status.value if hasattr(b.status,'value') else b.status,
            "fecha_creacion":   fecha_creacion,
            "fecha_estado":     fecha_estado,
            "total_rutas":      len(routes),
            "pending_routes":   pending_routes,
            "completed_routes": completed_routes,
            "total_stops":      total_stops,
            "completed_stops":  completed_stops,
            "total_cost":       round(total_cost, 2),
            "vehicle_type":     b.vehicle_type,
            "service_type":     b.service_type,
            "routes":           routes,
            "cancellation_reason": b.cancellation_reason,
            "cancelled_at":        b.cancelled_at.isoformat() if b.cancelled_at else None,
        })

    return result

@app.post("/api/v1/warehouse/batches/plan")
async def plan_batch(
    data:         DispatchPlanRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """
    Recibe N paradas sueltas y devuelve las rutas agrupadas propuestas
    por el dispatcher automático. NO persiste nada en la base de datos —
    el cliente puede ajustar los grupos antes de confirmar.
    """
    if not data.paradas:
        raise HTTPException(status_code=400, detail="No se enviaron paradas")

    company = get_current_company(current_user, db, x_company_id)
    max_rows = company.max_csv_rows or DEFAULT_MAX_CSV_ROWS
    if len(data.paradas) > max_rows:
        raise HTTPException(
            status_code=400,
            detail=f"Se enviaron {len(data.paradas)} paradas — el máximo permitido para {company.name} es {max_rows}.",
        )

    # Convertir Pydantic → dataclass interna del dispatcher
    stops = [
        DispatchStop(
            destino=p.destino, direccion=p.direccion, codigo_postal=p.codigo_postal,
            lat=p.lat, lng=p.lng,
            peso_lbs=p.peso_lbs, volumen_ft3=p.volumen_ft3,
            hora_limite=p.hora_limite,
            contacto=p.contacto, telefono=p.telefono,
            codigo_cliente=p.codigo_cliente, codigo_paquete=p.codigo_paquete, codigo_barras=p.codigo_barras,
            codigo_qr=p.codigo_qr, notas=p.notas,
            valor_declarado=p.valor_declarado, source=p.source,
            items=p.items,
        )
        for p in data.paradas
    ]

    planned = plan_stops(
        stops=stops,
        peso_max_lbs=data.peso_max_lbs,
        volumen_max_ft3=data.volumen_max_ft3,
        max_stops_per_route=data.max_stops_per_route,
    )

    return {
        "total_paradas": len(stops),
        "total_rutas":   len(planned),
        "vehicle_type":  data.vehicle_type,
        "service_type":  data.service_type,
        "peso_max_lbs":  data.peso_max_lbs,
        "volumen_max_ft3": data.volumen_max_ft3,
        "rutas": [planned_route_to_dict(r) for r in planned],
    }


def _create_route_detail(db: Session, route_header_id, seq: int, stop: dict, eta_scheduled: datetime = None) -> "RouteDetail":
    """
    Crea una parada (RouteDetail) + sus ShipmentItem(s) a partir de un dict con
    la forma de StopInput (mismo shape que usa el dispatcher, el CSV y el alta
    manual). Soporta varios paquetes por parada vía stop["items"]; si viene
    vacío, cae al comportamiento legacy de un único ShipmentItem por parada.
    Devuelve el RouteDetail ya creado (flushed, con id asignado). Los
    agregados (packages_count/weight_lbs/etc. en RouteHeader/RouteDetail) los
    recalculan solos los triggers de Postgres — no hace falta tocarlos acá.
    """
    items_data = stop.get("items") or []
    packages_count = len(items_data) if items_data else 1
    cargo_value = (
        sum(float(it.get("declared_value") or 0) for it in items_data)
        if items_data else stop.get("valor_declarado", 0)
    )

    detail = RouteDetail(
        id=uuid.uuid4(),
        route_header_id=route_header_id,
        sequence_order=seq,
        company_name=stop.get("destino", ""),
        address_line1=stop.get("direccion", ""),
        city="",
        state="",
        zip_code=stop.get("codigo_postal") or None,
        codigo_cliente=stop.get("codigo_cliente") or None,
        lat=stop.get("lat"),
        lng=stop.get("lng"),
        contact_name=stop.get("contacto", ""),
        contact_phone=stop.get("telefono", ""),
        weight_lbs=stop.get("peso_lbs", 0),
        volume_ft3=stop.get("volumen_ft3", 0),
        packages_count=packages_count,
        cargo_value=cargo_value,
        eta_scheduled=eta_scheduled,
        access_notes=stop.get("notas", ""),
        status=StopStatus.PENDING,
    )
    db.add(detail)
    db.flush()

    if items_data:
        # Varios paquetes en esta parada — un ShipmentItem por item
        for line_no, it in enumerate(items_data, start=1):
            db.add(ShipmentItem(
                id=uuid.uuid4(),
                route_detail_id=detail.id,
                line_number=line_no,
                package_code=it.get("package_code") or f"PKG-{detail.id.hex[:6].upper()}-{line_no}",
                barcode=it.get("barcode") or None,
                qr_code=it.get("qr_code") or None,
                declared_value=it.get("declared_value") or 0,
                weight_lbs=it.get("weight_lbs") or 0,
                volume_ft3=it.get("volume_ft3") or 0,
            ))
    else:
        # Comportamiento legacy: un único ShipmentItem por parada
        db.add(ShipmentItem(
            id=uuid.uuid4(),
            route_detail_id=detail.id,
            line_number=1,
            package_code=stop.get("codigo_paquete") or f"PKG-{detail.id.hex[:6].upper()}",
            barcode=stop.get("codigo_barras") or None,
            qr_code=stop.get("codigo_qr") or None,
            declared_value=stop.get("valor_declarado", 0),
            weight_lbs=stop.get("peso_lbs", 0),
            volume_ft3=stop.get("volumen_ft3", 0),
        ))

    return detail


@app.post("/api/v1/warehouse/batches/confirm", status_code=201)
async def confirm_batch(
    data:         DispatchConfirmRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Confirma los grupos propuestos (con los ajustes manuales del usuario)
    y los persiste en la DB: crea RouteHeaders, RouteDetails, ShipmentItems,
    y finalmente el RouteBatch que los agrupa.
    """
    # Resolver la empresa del admin
    company_admin = db.execute(
        select(CompanyAdmin).where(CompanyAdmin.user_id == current_user.id)
    ).scalar_one_or_none()
    if not company_admin:
        raise HTTPException(status_code=403, detail="Solo admins de empresa pueden crear lotes")

    company_id  = company_admin.company_id
    contract_id = uuid.UUID(data.contract_id) if data.contract_id else None
    fecha       = datetime.fromisoformat(data.fecha).date()

    # Obtener warehouse de origen (primer warehouse de la empresa)
    warehouse = db.execute(
        select(Warehouse).where(Warehouse.company_id == company_id)
    ).scalar_one_or_none()
    origin_warehouse_id = warehouse.id if warehouse else None

    # Parsear scheduled_start / scheduled_end como datetime del día indicado
    def to_dt(hhmm: str) -> datetime:
        h, m = hhmm.split(":")
        return datetime.combine(fecha, __import__("datetime").time(int(h), int(m)))

    sched_start = to_dt(data.scheduled_start)
    sched_end   = to_dt(data.scheduled_end)

    created_routes = []

    # Resolver service_type_id desde el código
    svc_type = db.execute(
        select(ServiceType).where(ServiceType.code == data.service_type)
    ).scalar_one_or_none()
    svc_type_id = svc_type.id if svc_type else None

    for grupo in data.grupos:
        stops_data = grupo.get("stops", [])
        if not stops_data:
            continue

        # Generar correlativo de ruta
        count = db.execute(select(RouteHeader)).scalars()
        route_number = f"ORL-{fecha.strftime('%Y%m%d')}-{uuid.uuid4().hex[:5].upper()}"

        # ── Crear RouteHeader ────────────────────────────────────────────────
        route = RouteHeader(
            id=uuid.uuid4(),
            company_id=company_id,
            origin_warehouse_id=origin_warehouse_id,
            contract_id=contract_id,
            route_number=route_number,
            title=f"{data.nombre} — Ruta {grupo.get('route_index', 0) + 1}",
            service_mode=data.service_type,
            service_type_id=svc_type_id,
            status=RouteStatus.DRAFT,
            scheduled_date=fecha,
            scheduled_start=sched_start,
            scheduled_end=sched_end,
            required_vehicle_type=data.vehicle_type,
            gross_pay=0.0,
            muevo_commission_amt=0.0,
        )
        db.add(route)
        db.flush()

        # ── Crear RouteDetails + ShipmentItems ───────────────────────────────
        stop_client_codes = []
        for seq, stop in enumerate(stops_data, start=1):
            _create_route_detail(db, route.id, seq, stop, eta_scheduled=to_dt(stop.get("hora_limite", "23:59")))
            stop_client_codes.append(stop.get("codigo_cliente") or "")

        # Propagar client_code a la ruta si todas las paradas son del mismo cliente
        unique_clients = set(c for c in stop_client_codes if c)
        route.client_code = unique_clients.pop() if len(unique_clients) == 1 else None

        # Precio inicial = fórmula de precio sugerido (peso/volumen ya
        # agregados por los triggers de Postgres tras crear las paradas)
        db.refresh(route)
        _apply_formula_price(route, svc_type)

        created_routes.append(route)

    # ── Crear RouteBatch ─────────────────────────────────────────────────────
    client_id_uuid = uuid.UUID(data.client_id) if data.client_id else None
    batch = RouteBatch(
        id=uuid.uuid4(),
        company_id=company_id,
        contract_id=contract_id,
        client_id=client_id_uuid,
        batch_number=next_batch_number(company_id, db),
        vehicle_type=data.vehicle_type or None,
        service_type=data.service_type or None,
        status=BatchStatus.DRAFT,
        notes=data.nombre,
    )
    db.add(batch)
    db.flush()

    # Propagar client_code al lote si todas las rutas son del mismo cliente
    route_client_codes = set(r.client_code for r in created_routes if r.client_code)
    batch.client_code = route_client_codes.pop() if len(route_client_codes) == 1 else None

    for route in created_routes:
        db.add(RouteBatchItem(
            id=uuid.uuid4(),
            batch_id=batch.id,
            route_header_id=route.id,
            status=BatchItemStatus.PENDING,
        ))

    db.commit()

    return {
        "status":    "created",
        "batch_id":  str(batch.id),
        "routes":    len(created_routes),
        "route_ids": [str(r.id) for r in created_routes],
    }


# ─── Agregar / editar / borrar rutas y paradas de un lote en borrador ────────
#
# Toda ruta pertenece a un lote sin excepción (regla de negocio existente),
# así que "borrar una ruta del lote" es borrarla por completo, no desvincularla.
# Todo esto solo se permite mientras el lote está en draft — una vez aprobado
# ya se ofertó a transporte y queda fijo.

def _require_draft_batch_access(db: Session, batch_id, current_user: User) -> "RouteBatch":
    """Verifica que el usuario administre la company dueña del lote y que el lote esté en draft."""
    company_admin = db.execute(
        select(CompanyAdmin).where(CompanyAdmin.user_id == current_user.id)
    ).scalar_one_or_none()
    if not company_admin:
        raise HTTPException(status_code=403, detail="Solo admins de empresa pueden modificar lotes")

    batch = db.get(RouteBatch, batch_id)
    if not batch or batch.company_id != company_admin.company_id:
        raise HTTPException(status_code=404, detail="Lote no encontrado")

    if batch.status != BatchStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Solo se pueden modificar lotes en borrador")

    return batch


def _get_route_in_draft_batch(db: Session, route_id, current_user: User) -> "RouteHeader":
    """Resuelve una ruta y valida que el lote al que pertenece esté en draft y sea del usuario."""
    route = db.get(RouteHeader, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")

    item = db.execute(
        select(RouteBatchItem).where(RouteBatchItem.route_header_id == route.id)
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="La ruta no pertenece a ningún lote")

    _require_draft_batch_access(db, item.batch_id, current_user)
    return route


class AddRouteToBatchRequest(BaseModel):
    title:                 str = Field(min_length=1)
    service_mode:          str  # mensajeria | logistica | paqueteria | empleados
    required_vehicle_type: Optional[str] = None
    scheduled_date:        str  # ISO date YYYY-MM-DD
    scheduled_start:       str = Field(pattern=HORA_PATTERN)
    scheduled_end:         str = Field(pattern=HORA_PATTERN)
    stops:                 list[StopInput] = Field(min_length=1)


class UpdateRouteRequest(BaseModel):
    title:                 Optional[str] = Field(None, min_length=1)
    scheduled_date:        Optional[str] = None
    scheduled_start:       Optional[str] = Field(None, pattern=HORA_PATTERN)
    scheduled_end:         Optional[str] = Field(None, pattern=HORA_PATTERN)
    required_vehicle_type: Optional[str] = None


class UpdateStopRequest(BaseModel):
    destino:        Optional[str] = Field(None, min_length=1)
    direccion:      Optional[str] = Field(None, min_length=1)
    codigo_postal:  Optional[str] = None
    lat:            Optional[float] = Field(None, ge=-90, le=90)
    lng:            Optional[float] = Field(None, ge=-180, le=180)
    hora_limite:    Optional[str] = Field(None, pattern=HORA_PATTERN)
    notas:          Optional[str] = None
    codigo_cliente: Optional[str] = None
    items:          Optional[list[StopItemInput]] = None


@app.post("/api/v1/warehouse/batches/{batch_id}/routes", status_code=201)
async def add_route_to_batch(
    batch_id:     str,
    data:         AddRouteToBatchRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Agrega una ruta nueva a un lote existente en draft."""
    batch = _require_draft_batch_access(db, uuid.UUID(batch_id), current_user)

    fecha = datetime.fromisoformat(data.scheduled_date).date()

    def to_dt(hhmm: str) -> datetime:
        h, m = hhmm.split(":")
        return datetime.combine(fecha, __import__("datetime").time(int(h), int(m)))

    warehouse = db.execute(
        select(Warehouse).where(Warehouse.company_id == batch.company_id)
    ).scalar_one_or_none()

    svc_type = db.execute(
        select(ServiceType).where(ServiceType.code == data.service_mode)
    ).scalar_one_or_none()

    route = RouteHeader(
        id=uuid.uuid4(),
        company_id=batch.company_id,
        origin_warehouse_id=warehouse.id if warehouse else None,
        contract_id=batch.contract_id,
        route_number=f"ORL-{fecha.strftime('%Y%m%d')}-{uuid.uuid4().hex[:5].upper()}",
        title=data.title,
        service_mode=data.service_mode,
        service_type_id=svc_type.id if svc_type else None,
        status=RouteStatus.DRAFT,
        scheduled_date=fecha,
        scheduled_start=to_dt(data.scheduled_start),
        scheduled_end=to_dt(data.scheduled_end),
        required_vehicle_type=data.required_vehicle_type,
        gross_pay=0.0,
        muevo_commission_amt=0.0,
    )
    db.add(route)
    db.flush()

    stop_client_codes = []
    for seq, stop in enumerate(data.stops, start=1):
        _create_route_detail(db, route.id, seq, stop.model_dump(), eta_scheduled=to_dt(stop.hora_limite))
        stop_client_codes.append(stop.codigo_cliente or "")
    unique_clients = set(c for c in stop_client_codes if c)
    route.client_code = unique_clients.pop() if len(unique_clients) == 1 else None

    db.add(RouteBatchItem(
        id=uuid.uuid4(), batch_id=batch.id, route_header_id=route.id,
        status=BatchItemStatus.PENDING,
    ))

    db.flush()
    db.refresh(route)
    _apply_formula_price(route, svc_type)  # precio inicial = fórmula de precio sugerido
    db.commit()
    db.refresh(route)
    return serialize_route_header(route)


@app.patch("/api/v1/warehouse/routes/{route_id}")
async def update_route(
    route_id:     str,
    data:         UpdateRouteRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Edita datos generales de una ruta (título, horario, vehículo). Solo si su lote está en draft."""
    route = _get_route_in_draft_batch(db, uuid.UUID(route_id), current_user)

    if data.title is not None:
        route.title = data.title
    if data.required_vehicle_type is not None:
        route.required_vehicle_type = data.required_vehicle_type

    fecha = route.scheduled_date.date() if isinstance(route.scheduled_date, datetime) else route.scheduled_date
    if data.scheduled_date is not None:
        fecha = datetime.fromisoformat(data.scheduled_date).date()
        route.scheduled_date = fecha
    if data.scheduled_start is not None:
        h, m = data.scheduled_start.split(":")
        route.scheduled_start = datetime.combine(fecha, __import__("datetime").time(int(h), int(m)))
    if data.scheduled_end is not None:
        h, m = data.scheduled_end.split(":")
        route.scheduled_end = datetime.combine(fecha, __import__("datetime").time(int(h), int(m)))

    db.commit()
    db.refresh(route)
    return serialize_route_header(route)


@app.delete("/api/v1/warehouse/batches/{batch_id}/routes/{route_id}", status_code=204)
async def delete_route_from_batch(
    batch_id:     str,
    route_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Borra una ruta completa (y sus paradas/items) de un lote en draft."""
    batch = _require_draft_batch_access(db, uuid.UUID(batch_id), current_user)

    item = db.execute(
        select(RouteBatchItem).where(
            RouteBatchItem.batch_id == batch.id,
            RouteBatchItem.route_header_id == uuid.UUID(route_id),
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="La ruta no pertenece a este lote")

    route = db.get(RouteHeader, uuid.UUID(route_id))

    # route_batch_items.route_header_id no tiene ON DELETE CASCADE — hay que
    # borrar el vínculo primero. route_headers sí cascadea a route_details
    # y de ahí a shipment_items.
    db.delete(item)
    db.flush()
    if route:
        db.delete(route)

    db.commit()


@app.post("/api/v1/warehouse/routes/{route_id}/stops", status_code=201)
async def add_stop_to_route(
    route_id:     str,
    data:         StopInput,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Agrega una parada a una ruta existente en draft."""
    route = _get_route_in_draft_batch(db, uuid.UUID(route_id), current_user)

    max_seq = db.execute(
        select(func.max(RouteDetail.sequence_order)).where(RouteDetail.route_header_id == route.id)
    ).scalar() or 0

    fecha = route.scheduled_date.date() if isinstance(route.scheduled_date, datetime) else route.scheduled_date
    h, m = data.hora_limite.split(":")
    eta = datetime.combine(fecha, __import__("datetime").time(int(h), int(m)))

    detail = _create_route_detail(db, route.id, max_seq + 1, data.model_dump(), eta_scheduled=eta)
    db.commit()
    db.refresh(detail)
    return serialize_route_detail(detail)


@app.patch("/api/v1/warehouse/routes/{route_id}/stops/{detail_id}")
async def update_stop(
    route_id:     str,
    detail_id:    str,
    data:         UpdateStopRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Edita una parada existente. Si `data` trae 'items', reemplaza todos sus paquetes."""
    route  = _get_route_in_draft_batch(db, uuid.UUID(route_id), current_user)
    detail = db.get(RouteDetail, uuid.UUID(detail_id))
    if not detail or detail.route_header_id != route.id:
        raise HTTPException(status_code=404, detail="Parada no encontrada en esta ruta")

    if data.destino is not None:
        detail.company_name = data.destino
    if data.direccion is not None:
        detail.address_line1 = data.direccion
    if data.codigo_postal is not None:
        detail.zip_code = data.codigo_postal or None
    if data.lat is not None:
        detail.lat = data.lat
    if data.lng is not None:
        detail.lng = data.lng
    if data.notas is not None:
        detail.access_notes = data.notas
    if data.codigo_cliente is not None:
        detail.codigo_cliente = data.codigo_cliente or None
    if data.hora_limite is not None:
        h, m = data.hora_limite.split(":")
        fecha = route.scheduled_date.date() if isinstance(route.scheduled_date, datetime) else route.scheduled_date
        detail.eta_scheduled = datetime.combine(fecha, __import__("datetime").time(int(h), int(m)))

    if data.items is not None:
        old_items = db.execute(
            select(ShipmentItem).where(ShipmentItem.route_detail_id == detail.id)
        ).scalars().all()
        for oi in old_items:
            db.delete(oi)
        db.flush()
        for line_no, it in enumerate(data.items, start=1):
            db.add(ShipmentItem(
                id=uuid.uuid4(),
                route_detail_id=detail.id,
                line_number=line_no,
                package_code=it.package_code or f"PKG-{detail.id.hex[:6].upper()}-{line_no}",
                barcode=it.barcode or None,
                qr_code=it.qr_code or None,
                declared_value=it.declared_value,
                weight_lbs=it.weight_lbs,
                volume_ft3=it.volume_ft3,
            ))

    db.commit()
    db.refresh(detail)
    return serialize_route_detail(detail)


@app.delete("/api/v1/warehouse/routes/{route_id}/stops/{detail_id}", status_code=204)
async def delete_stop(
    route_id:     str,
    detail_id:    str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Borra una parada de una ruta en draft (cascada a sus ShipmentItem)."""
    route  = _get_route_in_draft_batch(db, uuid.UUID(route_id), current_user)
    detail = db.get(RouteDetail, uuid.UUID(detail_id))
    if not detail or detail.route_header_id != route.id:
        raise HTTPException(status_code=404, detail="Parada no encontrada en esta ruta")

    remaining = db.execute(
        select(func.count()).select_from(RouteDetail).where(RouteDetail.route_header_id == route.id)
    ).scalar()
    if remaining <= 1:
        raise HTTPException(
            status_code=400,
            detail="La ruta necesita al menos una parada — borrala junto con la ruta si ya no hace falta",
        )

    db.delete(detail)
    db.commit()


# ─── Pydantic models — Batches & Incidents ────────────────────────────────────

class BatchCreate(BaseModel):
    route_ids:  list[str]
    notes:      Optional[str] = None
    expires_at: Optional[str] = None  # ISO datetime string

class BatchOffer(BaseModel):
    transport_company_id: str
    expires_at: Optional[str] = None

class BatchItemAction(BaseModel):
    action:      str   # "accept" | "reject"
    vehicle_id:  Optional[str] = None  # requerido para accept

class IncidentCreate(BaseModel):
    incident_type_id:  str
    route_header_id:   Optional[str] = None
    route_detail_id:   Optional[str] = None
    batch_item_id:     Optional[str] = None
    title:             str
    description:       str
    evidence_urls:     list[str] = []

class IncidentResolve(BaseModel):
    status:            str   # "resolved" | "unresolved"
    resolution_notes:  str


# ─── Incident Types CRUD ──────────────────────────────────────────────────────

class IncidentTypeCreate(BaseModel):
    code:                str
    name:                str
    severity:            str  # low | medium | high | critical
    affects_route_status: bool = False
    is_active:           bool = True

class IncidentTypeUpdate(BaseModel):
    name:                Optional[str]  = None
    severity:            Optional[str]  = None
    affects_route_status: Optional[bool] = None
    is_active:           Optional[bool] = None


def serialize_incident_type(t: IncidentType) -> dict:
    return {
        "id":                   str(t.id),
        "code":                 t.code,
        "name":                 t.name,
        "severity":             t.severity.value if hasattr(t.severity, 'value') else t.severity,
        "affects_route_status": t.affects_route_status,
        "is_active":            t.is_active,
    }


def serialize_incident(inc: Incident, db: Session) -> dict:
    itype    = db.get(IncidentType, inc.incident_type_id)
    reporter = db.get(User, inc.reported_by_user_id)
    resolver = db.get(User, inc.resolved_by_user_id) if inc.resolved_by_user_id else None
    route    = db.get(RouteHeader, inc.route_header_id) if inc.route_header_id else None
    return {
        "id":               str(inc.id),
        "type_id":          str(inc.incident_type_id),
        "type_name":        itype.name     if itype    else None,
        "type_code":        itype.code     if itype    else None,
        "severity":         itype.severity.value if itype and hasattr(itype.severity,'value') else None,
        "affects_route":    itype.affects_route_status if itype else False,
        "status":           inc.status.value if hasattr(inc.status, 'value') else inc.status,
        "title":            inc.title,
        "description":      inc.description,
        "evidence_urls":    inc.evidence_urls or [],
        "reporter_name":    reporter.full_name if reporter else None,
        "reporter_role":    inc.reporter_role.value if hasattr(inc.reporter_role,'value') else inc.reporter_role,
        "resolver_name":    resolver.full_name if resolver else None,
        "route_number":     route.route_number if route else None,
        "route_id":         str(inc.route_header_id) if inc.route_header_id else None,
        "resolution_notes": inc.resolution_notes,
        "reported_at":      inc.reported_at.isoformat() if inc.reported_at else None,
        "resolved_at":      inc.resolved_at.isoformat() if inc.resolved_at else None,
    }


@app.post("/api/v1/admin/incident-types", status_code=201)
async def create_incident_type(
    data:         IncidentTypeCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Crea un nuevo tipo de incidencia en el catálogo."""
    require_permission(current_user, db, "incidents", "create")
    existing = db.execute(
        select(IncidentType).where(IncidentType.code == data.code)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"Ya existe un tipo con código '{data.code}'")

    try:
        severity = IncidentSeverity(data.severity)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Severidad inválida: {data.severity}")

    t = IncidentType(
        id=uuid.uuid4(), code=data.code, name=data.name,
        severity=severity, affects_route_status=data.affects_route_status,
        is_active=data.is_active,
    )
    db.add(t); db.commit()
    return serialize_incident_type(t)


@app.patch("/api/v1/admin/incident-types/{type_id}")
async def update_incident_type(
    type_id:      str,
    data:         IncidentTypeUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Actualiza un tipo de incidencia existente."""
    require_permission(current_user, db, "incidents", "edit")
    t = db.get(IncidentType, uuid.UUID(type_id))
    if not t:
        raise HTTPException(status_code=404, detail="Tipo de incidencia no encontrado")

    if data.name                 is not None: t.name                 = data.name
    if data.affects_route_status is not None: t.affects_route_status = data.affects_route_status
    if data.is_active            is not None: t.is_active            = data.is_active
    if data.severity             is not None:
        try:
            t.severity = IncidentSeverity(data.severity)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Severidad inválida: {data.severity}")

    db.commit()
    return serialize_incident_type(t)


@app.get("/api/v1/warehouse/incidents")
async def warehouse_list_incidents(
    status:       Optional[str] = None,
    route_id:     Optional[str] = None,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Lista incidencias de la compañía activa, filtradas por estado o ruta."""
    company = get_current_company(current_user, db, x_company_id)

    # Obtener IDs de rutas de la compañía
    route_ids = db.execute(
        select(RouteHeader.id).where(RouteHeader.company_id == company.id)
    ).scalars().all()

    query = select(Incident).where(
        Incident.route_header_id.in_(route_ids)
    ).order_by(Incident.reported_at.desc())

    if status:
        try:
            query = query.where(Incident.status == IncidentStatus(status))
        except ValueError:
            pass
    if route_id:
        query = query.where(Incident.route_header_id == uuid.UUID(route_id))

    incidents = db.execute(query).scalars().all()
    return [serialize_incident(i, db) for i in incidents]


@app.post("/api/v1/warehouse/incidents", status_code=201)
async def warehouse_report_incident(
    data:         IncidentCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Reporta una incidencia desde el portal warehouse."""
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "incidents", "create", company.id)

    # Validar que la ruta pertenece a la compañía
    if data.route_header_id:
        route = db.get(RouteHeader, uuid.UUID(data.route_header_id))
        if not route or route.company_id != company.id:
            raise HTTPException(status_code=403, detail="La ruta no pertenece a tu compañía")

    incident_type = db.get(IncidentType, uuid.UUID(data.incident_type_id))
    if not incident_type:
        raise HTTPException(status_code=404, detail="Tipo de incidencia no encontrado")

    incident = Incident(
        id=uuid.uuid4(),
        incident_type_id=incident_type.id,
        reported_by_user_id=current_user.id,
        reporter_role=ReporterRole.WAREHOUSE_ADMIN,
        route_header_id=uuid.UUID(data.route_header_id) if data.route_header_id else None,
        route_detail_id=uuid.UUID(data.route_detail_id) if data.route_detail_id else None,
        status=IncidentStatus.OPEN,
        title=data.title,
        description=data.description,
        evidence_urls=data.evidence_urls or [],
    )
    db.add(incident)

    if incident_type.affects_route_status and data.route_header_id:
        route = db.get(RouteHeader, uuid.UUID(data.route_header_id))
        if route and route.status == RouteStatus.IN_PROGRESS:
            route.status = RouteStatus.INCIDENT_REPORTED

    db.commit()
    return serialize_incident(incident, db)


@app.patch("/api/v1/warehouse/incidents/{incident_id}/status")
async def warehouse_update_incident_status(
    incident_id:  str,
    data:         IncidentResolve,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Actualiza el estado de una incidencia desde el portal warehouse."""
    company = get_current_company(current_user, db, x_company_id)
    require_permission(current_user, db, "incidents", "edit", company.id)
    incident = db.get(Incident, uuid.UUID(incident_id))
    if not incident:
        raise HTTPException(status_code=404, detail="Incidencia no encontrada")

    # Validar que la ruta pertenece a la compañía
    if incident.route_header_id:
        route = db.get(RouteHeader, incident.route_header_id)
        if not route or route.company_id != company.id:
            raise HTTPException(status_code=403, detail="No tenés acceso a esta incidencia")

    valid = {
        "open":         ["under_review"],
        "under_review": ["resolved", "unresolved"],
    }
    current = incident.status.value if hasattr(incident.status,'value') else incident.status
    if data.status not in valid.get(current, []):
        raise HTTPException(status_code=400, detail=f"Transición inválida: {current} → {data.status}")

    incident.status           = IncidentStatus(data.status)
    incident.resolution_notes = data.resolution_notes
    incident.resolved_by_user_id = current_user.id

    if data.status in ("resolved", "unresolved"):
        incident.resolved_at = datetime.utcnow()
        if data.status == "resolved" and incident.route_header_id:
            route = db.get(RouteHeader, incident.route_header_id)
            if route and route.status == RouteStatus.INCIDENT_REPORTED:
                route.status = RouteStatus.IN_PROGRESS

    db.commit()
    return serialize_incident(incident, db)


def next_batch_number(company_id: uuid.UUID, db: Session) -> str:
    """Genera el siguiente número secuencial de lote: ORL-LOT-0001, 0002, ..."""
    last = db.execute(
        select(RouteBatch.batch_number)
        .where(RouteBatch.company_id == company_id, RouteBatch.batch_number.isnot(None))
        .order_by(RouteBatch.batch_number.desc())
        .limit(1)
    ).scalar_one_or_none()

    if last:
        try:
            n = int(last.split('-')[-1]) + 1
        except ValueError:
            n = 1
    else:
        n = 1
    return f"ORL-LOT-{n:04d}"


# ─── Route Batches ────────────────────────────────────────────────────────────

@app.post("/api/v1/batches", status_code=201)
async def create_batch(
    data:         BatchCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Dispatcher crea un lote de rutas para ofrecerlo a una empresa de transporte."""
    company_admin = db.execute(
        select(CompanyAdmin).where(CompanyAdmin.user_id == current_user.id)
    ).scalar_one_or_none()
    if not company_admin:
        raise HTTPException(status_code=403, detail="Solo admins de empresa pueden crear lotes")

    # Validar que todas las rutas pertenezcan a la empresa del admin
    route_ids = [uuid.UUID(r) for r in data.route_ids]
    routes = db.execute(
        select(RouteHeader).where(
            RouteHeader.id.in_(route_ids),
            RouteHeader.company_id == company_admin.company_id,
        )
    ).scalars().all()

    if len(routes) != len(route_ids):
        raise HTTPException(status_code=400, detail="Algunas rutas no pertenecen a tu empresa o no existen")

    # Validar que ninguna ruta ya pertenezca a otro lote
    for route_id in route_ids:
        existing = db.execute(
            select(RouteBatchItem).where(RouteBatchItem.route_header_id == route_id)
        ).scalar_one_or_none()
        if existing:
            route = db.get(RouteHeader, route_id)
            raise HTTPException(
                status_code=400,
                detail=f"La ruta {route.route_number if route else route_id} ya pertenece a un lote"
            )

    batch = RouteBatch(
        id=uuid.uuid4(), company_id=company_admin.company_id,
        batch_number=next_batch_number(company_admin.company_id, db),
        status=BatchStatus.DRAFT, notes=data.notes,
        expires_at=datetime.fromisoformat(data.expires_at) if data.expires_at else None,
    )
    db.add(batch)
    db.flush()

    for route in routes:
        db.add(RouteBatchItem(
            id=uuid.uuid4(), batch_id=batch.id,
            route_header_id=route.id, status=BatchItemStatus.PENDING,
        ))

    db.commit()
    return {"status": "created", "batch_id": str(batch.id), "items": len(routes)}


@app.post("/api/v1/batches/{batch_id}/offer")
async def offer_batch(
    batch_id:     str,
    data:         BatchOffer,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Ofrece el lote a una empresa de transporte específica."""
    batch = db.get(RouteBatch, uuid.UUID(batch_id))
    if not batch or batch.status not in (BatchStatus.DRAFT, BatchStatus.PARTIALLY_ACCEPTED):
        raise HTTPException(status_code=400, detail="Lote no disponible para ofrecer")

    tc_id = uuid.UUID(data.transport_company_id)
    # Asignar solo los ítems que siguen pendientes / rechazados
    pending_items = db.execute(
        select(RouteBatchItem).where(
            RouteBatchItem.batch_id == batch.id,
            RouteBatchItem.status.in_([BatchItemStatus.PENDING, BatchItemStatus.REJECTED]),
        )
    ).scalars().all()

    if not pending_items:
        raise HTTPException(status_code=400, detail="No hay ítems pendientes en este lote")

    for item in pending_items:
        item.transport_company_id = tc_id
        item.status = BatchItemStatus.PENDING
        # Actualizar la ruta para que aparezca como ofrecida en el portal transportista
        route = db.get(RouteHeader, item.route_header_id)
        if route:
            route.transport_company_id = tc_id
            route.status = RouteStatus.PUBLISHED

    batch.status = BatchStatus.OFFERED
    batch.offered_at = datetime.utcnow()
    if data.expires_at:
        batch.expires_at = datetime.fromisoformat(data.expires_at)

    db.commit()
    return {"status": "offered", "batch_id": batch_id, "items_offered": len(pending_items)}


@app.post("/api/v1/batches/{batch_id}/items/{item_id}/respond")
async def respond_batch_item(
    batch_id:     str,
    item_id:      str,
    data:         BatchItemAction,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """La empresa de transporte acepta o rechaza un ítem individual del lote."""
    tc = get_current_transport_company(current_user, db)
    item = db.get(RouteBatchItem, uuid.UUID(item_id))

    if not item or str(item.batch_id) != batch_id:
        raise HTTPException(status_code=404, detail="Ítem no encontrado")
    if item.transport_company_id != tc.id:
        raise HTTPException(status_code=403, detail="Este ítem no está asignado a tu empresa")
    if item.status not in (BatchItemStatus.PENDING,):
        raise HTTPException(status_code=400, detail="Este ítem ya fue respondido")

    route = db.get(RouteHeader, item.route_header_id)

    if data.action == "accept":
        if data.vehicle_id:
            vehicle = db.get(Vehicle, uuid.UUID(data.vehicle_id))
            if not vehicle or vehicle.transport_company_id != tc.id:
                raise HTTPException(status_code=400, detail="Vehículo no válido")
            route.vehicle_id = vehicle.id
        item.status = BatchItemStatus.ACCEPTED
        item.assigned_at = datetime.utcnow()
        route.status = RouteStatus.ASSIGNED

    elif data.action == "reject":
        item.status = BatchItemStatus.REJECTED
        route.transport_company_id = None
        route.status = RouteStatus.PUBLISHED
        tc.rejected_routes = (tc.rejected_routes or 0) + 1
    else:
        raise HTTPException(status_code=400, detail="Acción inválida — usa 'accept' o 'reject'")

    # Recalcular status del lote completo
    _recalculate_batch_status(item.batch_id, db)
    db.commit()
    return {"status": data.action + "ed", "item_id": item_id, "batch_status": db.get(RouteBatch, uuid.UUID(batch_id)).status.value}


class BatchStatusChange(BaseModel):
    status: str  # "approved" | "cancelled"
    reason: Optional[str] = None  # obligatorio al anular un lote aprobado


@app.post("/api/v1/warehouse/batches/{batch_id}/status")
async def change_batch_status(
    batch_id:     str,
    data:         BatchStatusChange,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Cambia manualmente el estado de un lote.
    Solo se permiten transiciones manuales:
      draft     → approved
      draft     → cancelled
      approved  → cancelled
    """
    # Validar que el usuario tenga acceso a este lote
    batch = db.get(RouteBatch, uuid.UUID(batch_id))
    if not batch:
        raise HTTPException(status_code=404, detail="Lote no encontrado")

    # Verificar que el usuario es admin de la compañía del lote
    hu = db.execute(
        select(HoldingUser).where(
            HoldingUser.user_id == current_user.id,
            HoldingUser.is_active == True,
        )
    ).scalar_one_or_none()

    company_admin = db.execute(
        select(CompanyAdmin).where(CompanyAdmin.user_id == current_user.id)
    ).scalar_one_or_none()

    has_access = (
        (hu and hu.is_super_admin) or
        (company_admin and company_admin.company_id == batch.company_id)
    )
    if not has_access:
        raise HTTPException(status_code=403, detail="Sin acceso a este lote")

    BATCH_STATUS_PERMISSION = {"approved": "approve", "cancelled": "cancel"}
    require_permission(
        current_user, db, "batches",
        BATCH_STATUS_PERMISSION.get(data.status, "edit"), batch.company_id,
    )

    # Validar transición manual permitida
    MANUAL_TRANSITIONS = {
        "draft":    ["approved", "cancelled"],
        "approved": ["cancelled"],
    }
    current_status = batch.status.value if hasattr(batch.status, 'value') else batch.status
    allowed = MANUAL_TRANSITIONS.get(current_status, [])

    if data.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Transición no permitida: {current_status} → {data.status}. "
                   f"Transiciones manuales permitidas desde '{current_status}': {allowed}"
        )

    # Para aprobar el lote, todas sus rutas necesitan un precio ya fijado
    # (gross_pay != 0) — evita ofertar a transporte una ruta sin negociar.
    if data.status == "approved":
        route_ids = db.execute(
            select(RouteBatchItem.route_header_id).where(RouteBatchItem.batch_id == batch.id)
        ).scalars().all()
        routes_without_price = db.execute(
            select(RouteHeader.route_number).where(
                RouteHeader.id.in_(route_ids),
                RouteHeader.gross_pay == 0,
            )
        ).scalars().all()
        if routes_without_price:
            raise HTTPException(
                status_code=400,
                detail="No se puede aprobar el lote — hay rutas sin precio fijado (cerrá la "
                       "negociación primero): " + ", ".join(routes_without_price)
            )

        # Al aprobar, las rutas quedan disponibles en el marketplace para
        # que cualquier transportista pueda pujar sobre su precio actual.
        routes_to_open = db.execute(
            select(RouteHeader).where(RouteHeader.id.in_(route_ids))
        ).scalars().all()
        for route in routes_to_open:
            _open_route_marketplace(route, current_user, db)

    # Anular un lote ya aprobado es más serio que cancelar un borrador — exige motivo
    if data.status == "cancelled" and current_status == "approved":
        if not data.reason or not data.reason.strip():
            raise HTTPException(
                status_code=400,
                detail="Para anular un lote aprobado hay que indicar el motivo de la anulación"
            )
        batch.cancellation_reason = data.reason.strip()
        batch.cancelled_at        = datetime.utcnow()

    old_status   = batch.status.value if hasattr(batch.status, 'value') else batch.status
    batch.status = BatchStatus(data.status)
    batch.updated_at = datetime.utcnow()
    db.commit()

    # Notificar al cliente del lote
    status_labels = {
        "approved":             "✅ Lote aprobado",
        "cancelled":            "❌ Lote cancelado",
        "partially_accepted":   "⚡ Lote parcialmente aceptado",
        "fully_accepted":       "✅ Lote completamente aceptado",
        "partially_completed":  "🔄 Lote en curso",
        "completed":            "✅ Lote completado",
        "closed_with_incidents":"⚠️ Lote cerrado con incidencias",
    }
    label = status_labels.get(data.status, data.status)
    await notify_batch_client(
        batch_id=uuid.UUID(batch_id),
        event="batch_status",
        subject=f"{label} — {batch.notes or batch_id[:8]}",
        body_lines=[
            f"El estado de tu lote ha cambiado.",
            f"<strong>Lote:</strong> {batch.notes or batch_id}",
            f"<strong>Estado anterior:</strong> {old_status}",
            f"<strong>Estado actual:</strong> {data.status}",
            f"<strong>Fecha:</strong> {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC",
        ] + ([f"<strong>Motivo de la anulación:</strong> {batch.cancellation_reason}"] if batch.cancellation_reason else []),
        db=db,
    )

    return {
        "status":              "updated",
        "batch_id":            batch_id,
        "new_status":          data.status,
        "cancellation_reason": batch.cancellation_reason,
        "cancelled_at":        batch.cancelled_at.isoformat() if batch.cancelled_at else None,
    }


@app.get("/api/v1/batches/{batch_id}")
async def get_batch(
    batch_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Detalle completo de un lote con todos sus ítems."""
    batch = db.get(RouteBatch, uuid.UUID(batch_id))
    if not batch:
        raise HTTPException(status_code=404, detail="Lote no encontrado")

    items = []
    for item in batch.items:
        route = db.get(RouteHeader, item.route_header_id)
        tc_name = None
        if item.transport_company_id:
            tc = db.get(TransportCompany, item.transport_company_id)
            tc_name = tc.name if tc else None
        items.append({
            "item_id": str(item.id), "route_id": str(item.route_header_id),
            "route_number": route.route_number if route else None,
            "route_title": route.title if route else None,
            "status": item.status.value,
            "transport_company": tc_name,
            "assigned_at": item.assigned_at.isoformat() if item.assigned_at else None,
            "incident_id": str(item.incident_id) if item.incident_id else None,
        })

    return {
        "batch_id": str(batch.id), "status": batch.status.value,
        "notes": batch.notes,
        "offered_at": batch.offered_at.isoformat() if batch.offered_at else None,
        "expires_at": batch.expires_at.isoformat() if batch.expires_at else None,
        "items": items,
        "total": len(items),
        "accepted": sum(1 for i in items if i["status"] in ("accepted", "completed", "closed_with_incidents")),
        "pending": sum(1 for i in items if i["status"] == "pending"),
        "rejected": sum(1 for i in items if i["status"] == "rejected"),
    }


@app.get("/api/v1/transport/batches")
async def transport_list_batches(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Lista los lotes asignados a la empresa de transporte del usuario actual."""
    tc = get_current_transport_company(current_user, db)

    items = db.execute(
        select(RouteBatchItem)
        .where(RouteBatchItem.transport_company_id == tc.id)
        .where(RouteBatchItem.status.in_([
            BatchItemStatus.PENDING, BatchItemStatus.ACCEPTED, BatchItemStatus.REASSIGNED
        ]))
    ).scalars().all()

    # Agrupar por lote
    batch_ids = list({item.batch_id for item in items})
    result = []
    for bid in batch_ids:
        batch = db.get(RouteBatch, bid)
        batch_items = [i for i in items if i.batch_id == bid]
        result.append({
            "batch_id": str(bid), "status": batch.status.value,
            "notes": batch.notes,
            "expires_at": batch.expires_at.isoformat() if batch.expires_at else None,
            "items": len(batch_items),
            "pending": sum(1 for i in batch_items if i.status == BatchItemStatus.PENDING),
        })
    return result


# ─── Incidents ────────────────────────────────────────────────────────────────

@app.get("/api/v1/incidents/types")
async def list_incident_types(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Catálogo de tipos de incidencia activos."""
    types = db.execute(
        select(IncidentType).where(IncidentType.is_active == True)
    ).scalars().all()
    return [
        {"id": str(t.id), "code": t.code, "name": t.name,
         "severity": t.severity.value, "affects_route_status": t.affects_route_status}
        for t in types
    ]


@app.post("/api/v1/incidents", status_code=201)
async def report_incident(
    data:         IncidentCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Reporta una nueva incidencia. Puede ser llamado por conductores,
    admins de empresa de transporte, o admins warehouse.
    Automáticamente:
      1. Crea el registro de incidencia
      2. Si el tipo afecta el estado de la ruta → marca la ruta como incident_reported
      3. Notifica por push al conductor, admin TC y admin warehouse
    """
    # Determinar el rol del reportante
    reporter_role = ReporterRole.DRIVER
    if db.execute(select(CompanyAdmin).where(CompanyAdmin.user_id == current_user.id)).scalar_one_or_none():
        reporter_role = ReporterRole.WAREHOUSE_ADMIN
    elif db.execute(select(TransportCompanyAdmin).where(TransportCompanyAdmin.user_id == current_user.id)).scalar_one_or_none():
        reporter_role = ReporterRole.TRANSPORT_ADMIN

    incident_type = db.get(IncidentType, uuid.UUID(data.incident_type_id))
    if not incident_type:
        raise HTTPException(status_code=404, detail="Tipo de incidencia no encontrado")

    incident = Incident(
        id=uuid.uuid4(),
        incident_type_id=incident_type.id,
        reported_by_user_id=current_user.id,
        reporter_role=reporter_role,
        route_header_id=uuid.UUID(data.route_header_id) if data.route_header_id else None,
        route_detail_id=uuid.UUID(data.route_detail_id) if data.route_detail_id else None,
        batch_item_id=uuid.UUID(data.batch_item_id)    if data.batch_item_id    else None,
        status=IncidentStatus.OPEN,
        title=data.title, description=data.description,
        evidence_urls=data.evidence_urls,
    )
    db.add(incident)
    db.flush()

    # Cambiar estado de la ruta si aplica
    if incident_type.affects_route_status and data.route_header_id:
        route = db.get(RouteHeader, uuid.UUID(data.route_header_id))
        if route and route.status == RouteStatus.IN_PROGRESS:
            route.status = RouteStatus.INCIDENT_REPORTED

    # Notificaciones push (best-effort — no falla si no hay token)
    if data.route_header_id:
        route = db.get(RouteHeader, uuid.UUID(data.route_header_id))
        if route:
            push_tokens = []
            # Conductor
            if route.vehicle_id:
                vehicle = db.get(Vehicle, route.vehicle_id)
                if vehicle and vehicle.driver_id:
                    driver = db.get(Driver, vehicle.driver_id)
                    if driver and driver.stripe_account_id:  # token guardado aquí por ahora
                        push_tokens.append(driver.stripe_account_id)
            # Admin warehouse
            wh_admin = db.execute(
                select(CompanyAdmin).where(CompanyAdmin.company_id == route.company_id)
            ).scalar_one_or_none()
            if wh_admin:
                wh_user = db.get(User, wh_admin.user_id)
                # TC admin
            if route.transport_company_id:
                tc_admin = db.execute(
                    select(TransportCompanyAdmin).where(TransportCompanyAdmin.transport_company_id == route.transport_company_id)
                ).scalar_one_or_none()
                if tc_admin:
                    tc_user = db.get(User, tc_admin.user_id)

            # Fire and forget push notifications
            for token in push_tokens:
                try:
                    import httpx
                    httpx.post("https://exp.host/--/api/v2/push/send", json={
                        "to": token, "title": f"⚠️ Incidencia reportada",
                        "body": f"{incident_type.name} — {data.title}",
                    }, timeout=3)
                except Exception:
                    pass

    db.commit()

    # Notificar al cliente del lote sobre la incidencia
    if data.route_header_id:
        batch_item_for_notif = db.execute(
            select(RouteBatchItem).where(RouteBatchItem.route_header_id == uuid.UUID(data.route_header_id))
        ).scalar_one_or_none()
        if batch_item_for_notif:
            await notify_batch_client(
                batch_id=batch_item_for_notif.batch_id,
                event="incident",
                subject=f"⚠️ Incidencia reportada — {incident_type.name}",
                body_lines=[
                    f"Se ha reportado una incidencia en tu envío.",
                    f"<strong>Tipo:</strong> {incident_type.name}",
                    f"<strong>Severidad:</strong> {incident_type.severity.value}",
                    f"<strong>Título:</strong> {data.title}",
                    f"<strong>Descripción:</strong> {data.description}",
                    f"<strong>Reportado por:</strong> {reporter_role.value}",
                    f"<strong>Fecha:</strong> {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC",
                    "Nuestro equipo está trabajando en resolver esta situación.",
                ],
                db=db,
            )

    return {"status": "reported", "incident_id": str(incident.id), "reporter_role": reporter_role.value}


@app.patch("/api/v1/incidents/{incident_id}/status")
async def update_incident_status(
    incident_id:  str,
    data:         IncidentResolve,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Actualiza el estado de una incidencia (open → under_review → resolved/unresolved)."""
    incident = db.get(Incident, uuid.UUID(incident_id))
    if not incident:
        raise HTTPException(status_code=404, detail="Incidencia no encontrada")

    valid_transitions = {
        "open":         ["under_review"],
        "under_review": ["resolved", "unresolved"],
    }
    current = incident.status.value
    if data.status not in valid_transitions.get(current, []):
        raise HTTPException(status_code=400, detail=f"Transición inválida: {current} → {data.status}")

    incident.status = IncidentStatus(data.status)
    incident.resolution_notes = data.resolution_notes
    incident.resolved_by_user_id = current_user.id

    if data.status in ("resolved", "unresolved"):
        incident.resolved_at = datetime.utcnow()

        # Si la ruta estaba pausada por esta incidencia, reactivarla si se resolvió
        if data.status == "resolved" and incident.route_header_id:
            route = db.get(RouteHeader, incident.route_header_id)
            if route and route.status == RouteStatus.INCIDENT_REPORTED:
                route.status = RouteStatus.IN_PROGRESS

        # Si el ítem de lote estaba asociado → cerrarlo con incidencia si no se resolvió
        if data.status == "unresolved" and incident.batch_item_id:
            item = db.get(RouteBatchItem, incident.batch_item_id)
            if item:
                item.status = BatchItemStatus.CLOSED_WITH_INCIDENTS
                item.incident_id = incident.id
                _recalculate_batch_status(item.batch_id, db)

    db.commit()
    return {"status": "updated", "incident_status": data.status}


@app.get("/api/v1/incidents")
async def list_incidents(
    route_id:     Optional[str] = None,
    status:       Optional[str] = None,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Lista incidencias visibles al usuario actual, filtradas por ruta o estado."""
    query = select(Incident).order_by(Incident.reported_at.desc())
    if route_id:
        query = query.where(Incident.route_header_id == uuid.UUID(route_id))
    if status:
        query = query.where(Incident.status == IncidentStatus(status))

    incidents = db.execute(query).scalars().all()
    result = []
    for inc in incidents:
        itype = db.get(IncidentType, inc.incident_type_id)
        reporter = db.get(User, inc.reported_by_user_id)
        result.append({
            "id": str(inc.id),
            "type": itype.name if itype else None,
            "type_code": itype.code if itype else None,
            "severity": itype.severity.value if itype else None,
            "status": inc.status.value,
            "title": inc.title,
            "description": inc.description,
            "reporter": reporter.full_name if reporter else None,
            "reporter_role": inc.reporter_role.value,
            "route_header_id": str(inc.route_header_id) if inc.route_header_id else None,
            "evidence_urls": inc.evidence_urls,
            "reported_at": inc.reported_at.isoformat(),
            "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
            "resolution_notes": inc.resolution_notes,
        })
    return result


# ─── Helper: recalculate batch status based on its items ──────────────────────

def _recalculate_batch_status(batch_id: uuid.UUID, db: Session):
    """Recalcula y actualiza el status del lote según el estado de sus ítems."""
    batch = db.get(RouteBatch, batch_id)
    if not batch:
        return

    items = db.execute(
        select(RouteBatchItem).where(RouteBatchItem.batch_id == batch_id)
    ).scalars().all()

    statuses = [i.status for i in items]
    total = len(statuses)
    if total == 0:
        return

    accepted  = sum(1 for s in statuses if s in (BatchItemStatus.ACCEPTED, BatchItemStatus.COMPLETED, BatchItemStatus.CLOSED_WITH_INCIDENTS))
    completed = sum(1 for s in statuses if s == BatchItemStatus.COMPLETED)
    incidents = sum(1 for s in statuses if s == BatchItemStatus.CLOSED_WITH_INCIDENTS)
    pending   = sum(1 for s in statuses if s == BatchItemStatus.PENDING)

    if completed + incidents == total:
        batch.status = BatchStatus.CLOSED_WITH_INCIDENTS if incidents > 0 else BatchStatus.COMPLETED
    elif completed + incidents > 0:
        batch.status = BatchStatus.PARTIALLY_COMPLETED
    elif accepted == total:
        batch.status = BatchStatus.FULLY_ACCEPTED
    elif accepted > 0 and pending == 0:
        batch.status = BatchStatus.PARTIALLY_ACCEPTED
    elif accepted > 0:
        batch.status = BatchStatus.PARTIALLY_ACCEPTED


# ─── Seed data (desarrollo) ───────────────────────────────────────────────────

@app.post("/api/v1/dev/seed", include_in_schema=False)
async def seed_database(db: Session = Depends(get_db)):
    """
    Crea usuarios de prueba en la base de datos.
    Solo para desarrollo — no exponer en producción.
    """
    users_to_create = [
        {
            "email":    "carlos@muevo.app",
            "password": "muevo123",
            "name":     "Carlos Rodriguez",
            "role":     UserRole.DRIVER,
        },
        {
            "email":    "driver@muevo.app",
            "password": "muevo123",
            "name":     "Carlos Mendez",
            "role":     UserRole.DRIVER,
        },
        {
            "email":    "transport@muevo.app",
            "password": "muevo123",
            "name":     "Andrea Fuentes",
            "role":     UserRole.DRIVER,
        },
        {
            "email":    "admin@muevo.app",
            "password": "muevo123",
            "name":     "Admin Muevo",
            "role":     UserRole.MUEVO_ADMIN,
        },
    ]

    created = []
    for u in users_to_create:
        existing = db.execute(
            select(User).where(User.email == u["email"])
        ).scalar_one_or_none()

        if existing:
            created.append(f"{u['email']} — ya existe")
            continue

        initials = "".join(w[0].upper() for w in u["name"].split()[:2])
        user = User(
            id              = uuid.uuid4(),
            email           = u["email"],
            full_name       = u["name"],
            avatar_initials = initials,
            role            = u["role"],
            hashed_password = hash_password(u["password"]),
            is_active       = True,
            is_verified     = True,
        )
        db.add(user)
        db.flush()

        if u["role"] == UserRole.DRIVER:
            driver = Driver(
                id            = uuid.uuid4(),
                user_id       = user.id,
                is_online     = True,
                is_approved   = True,
                service_modes = ["mensajeria", "logistica", "empleados"],
                avg_rating    = 4.93,
            )
            db.add(driver)

        created.append(f"{u['email']} — creado OK")

    db.commit()
    return {"created": created}

# ─── Serializers ──────────────────────────────────────────────────────────────

def serialize_route_header(rh: "RouteHeader") -> dict:
    """Serializa un RouteHeader (modelo Holding -> Company -> Warehouse vigente)."""
    return {
        "id":                  str(rh.id),
        "route_number":        rh.route_number,
        "external_reference":  rh.external_reference,
        "title":               rh.title,
        "service_mode":        rh.service_mode.value if hasattr(rh.service_mode, 'value') else rh.service_mode,
        "status":              rh.status.value if hasattr(rh.status, 'value') else rh.status,
        "dispatch_mode":       rh.dispatch_mode,
        "scheduled_date":      rh.scheduled_date.isoformat() if rh.scheduled_date else None,
        "scheduled_start":     rh.scheduled_start.isoformat() if rh.scheduled_start else None,
        "scheduled_end":       rh.scheduled_end.isoformat() if rh.scheduled_end else None,
        "total_stops":         rh.total_stops,
        "completed_stops":     rh.completed_stops,
        "total_km":            float(rh.total_km_estimated or 0),
        # Detalle de envío (poblado automáticamente por triggers de PostgreSQL)
        "total_packages":      rh.total_packages,
        "total_weight_lbs":    float(rh.total_weight_lbs or 0),
        "total_volume_ft3":    float(rh.total_volume_ft3 or 0),
        "total_cargo_value":   float(rh.total_cargo_value or 0),
        "has_fragile_items":   rh.has_fragile_items,
        "has_hazmat_items":    rh.has_hazmat_items,
        "requires_temp_control": rh.requires_temp_control,
        "requires_insurance":  rh.requires_insurance,
        # Financiero
        "gross_pay":           float(rh.gross_pay),
        "negotiation_status":  rh.negotiation_status.value if hasattr(rh.negotiation_status, 'value') else rh.negotiation_status,
        "suggested_price":     float(rh.suggested_price) if rh.suggested_price else None,
        "margin_pct":          float(rh.margin_pct or 0),
        "company_name":        rh.company.name if rh.company else None,
        "origin_warehouse":    rh.origin_warehouse.name if rh.origin_warehouse else None,
        "driver_notes":        rh.driver_notes,
        "stops":               [serialize_route_detail(d) for d in (rh.details or [])],
    }


def serialize_route_detail(rd: "RouteDetail") -> dict:
    """Serializa una parada (RouteDetail) con su detalle de envío e items."""
    return {
        "id":                str(rd.id),
        "sequence_order":    rd.sequence_order,
        "status":            rd.status.value if hasattr(rd.status, 'value') else rd.status,
        "address":           rd.address_line1,
        "city":              rd.city,
        "state":             rd.state,
        "zip_code":          rd.zip_code,
        "lat":               float(rd.lat) if rd.lat is not None else None,
        "lng":               float(rd.lng) if rd.lng is not None else None,
        "company_name":      rd.company_name,
        "contact_name":      rd.contact_name,
        "contact_phone":     rd.contact_phone,
        "access_notes":      rd.access_notes,
        "destination_warehouse": rd.destination_warehouse.name if rd.destination_warehouse else None,
        # Detalle de envío de esta parada
        "packages_count":    rd.packages_count,
        "weight_lbs":        float(rd.weight_lbs or 0),
        "volume_ft3":        float(rd.volume_ft3 or 0),
        "cargo_value":       float(rd.cargo_value or 0),
        "is_fragile":        rd.is_fragile,
        "is_hazmat":         rd.is_hazmat,
        "is_temp_controlled": rd.is_temp_controlled,
        "dock_number":       rd.dock_number,
        "wait_minutes_estimated": rd.wait_minutes_estimated,
        "eta_scheduled":     rd.eta_scheduled.isoformat() if rd.eta_scheduled else None,
        "pod_required":      rd.pod_required.value if hasattr(rd.pod_required, 'value') else rd.pod_required,
        "pod_captured":      rd.delivery is not None,
        "passengers_count":  rd.passengers_count,
        "items":             [serialize_shipment_item(i) for i in (rd.items or [])],
    }


def serialize_shipment_item(item: "ShipmentItem") -> dict:
    """Serializa una línea individual de paquete/SKU dentro de una parada."""
    return {
        "id":              str(item.id),
        "line_number":     item.line_number,
        "package_code":    item.package_code,
        "description":     item.description,
        "item_type":       item.item_type,
        "quantity":        item.quantity,
        "weight_lbs":      float(item.weight_lbs or 0),
        "volume_ft3":      float(item.volume_ft3 or 0) if item.volume_ft3 else None,
        "declared_value":  float(item.declared_value or 0),
        "is_fragile":      item.is_fragile,
        "is_hazmat":       item.is_hazmat,
        "picked_up":       item.picked_up,
        "delivered":       item.delivered,
    }


def serialize_route(route: Route) -> dict:
    return {
        "id":               str(route.id),
        "route_number":     route.route_number,
        "title":            route.title,
        "service_mode":     route.service_mode.value if hasattr(route.service_mode, 'value') else route.service_mode,
        "status":           route.status.value if hasattr(route.status, 'value') else route.status,
        "scheduled_date":   route.scheduled_date.isoformat() if route.scheduled_date else None,
        "scheduled_start":  route.scheduled_start.isoformat() if route.scheduled_start else None,
        "scheduled_end":    route.scheduled_end.isoformat() if route.scheduled_end else None,
        "total_stops":      route.total_stops,
        "completed_stops":  route.completed_stops,
        "total_km":         float(route.total_km_estimated or 0),
        "gross_pay":        float(route.gross_pay),
        "net_pay":          float(route.net_pay_estimated or 0),
        "margin_pct":       float(route.margin_pct or 0),
        "driver_notes":     route.driver_notes,
        "stops":            [serialize_stop(s) for s in (route.stops or [])],
    }

def serialize_stop(stop: Stop) -> dict:
    return {
        "id":             str(stop.id),
        "sequence_order": stop.sequence_order,
        "status":         stop.status.value if hasattr(stop.status, 'value') else stop.status,
        "address":        stop.address_line1,
        "city":           stop.city,
        "company_name":   stop.company_name,
        "contact_name":   stop.contact_name,
        "contact_phone":  stop.contact_phone,
        "package_code":   stop.package_code,
        "eta_scheduled":  stop.eta_scheduled.isoformat() if stop.eta_scheduled else None,
        "pod_required":   stop.pod_required.value if hasattr(stop.pod_required, 'value') else stop.pod_required,
        "is_urgent":      stop.is_urgent,
        "access_notes":   stop.access_notes,
        "pod_captured":   stop.delivery is not None,
    }

def serialize_metric(m: DriverMetric) -> dict:
    return {
        "date":             m.metric_date.isoformat() if m.metric_date else None,
        "routes_completed": m.routes_completed,
        "stops_completed":  m.stops_completed,
        "km_driven":        float(m.km_driven),
        "net_earned":       float(m.net_earned),
        "avg_net_per_hour": float(m.avg_net_per_hour or 0),
        "on_time_rate":     float(m.on_time_rate or 0),
    }


@app.post("/api/v1/dev/seed-routes", include_in_schema=False)
async def seed_routes(db: Session = Depends(get_db)):
    """
    Crea datos de prueba para el conductor demo usando el modelo vigente:
    Holding -> Company -> Warehouse -> RouteHeader -> RouteDetail -> ShipmentItem
    """
    # Buscar el conductor demo
    driver_user = db.execute(
        select(User).where(User.email == "driver@muevo.app")
    ).scalar_one_or_none()
    if not driver_user:
        raise HTTPException(status_code=404, detail="Corré /dev/seed primero")

    driver = db.execute(
        select(Driver).where(Driver.user_id == driver_user.id)
    ).scalar_one_or_none()
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    # Evitar duplicar si ya existen rutas para este escenario demo
    existing = db.execute(
        select(RouteHeader).where(RouteHeader.route_number == "ORL-2026-000001")
    ).scalar_one_or_none()
    if existing:
        return {"status": "routes already exist"}

    now = datetime.utcnow()

    # ── Holding + Company demo ──────────────────────────────────────────────
    holding = db.execute(
        select(Holding).where(Holding.name == "LegalDocs Express LLC")
    ).scalar_one_or_none()
    if not holding:
        holding = Holding(
            id=uuid.uuid4(), name="LegalDocs Express LLC",
            holding_type="single_entity", billing_mode="per_company",
            primary_contact_email="ops@legaldocs.com",
        )
        db.add(holding)
        db.flush()

    company = db.execute(
        select(Company).where(Company.name == "LegalDocs Express LLC")
    ).scalar_one_or_none()
    if not company:
        company = Company(
            id=uuid.uuid4(), holding_id=holding.id, is_primary_company=True,
            name="LegalDocs Express LLC", industry="legal",
            contact_email="ops@legaldocs.com", city="New York", state="NY",
            country="US", allowed_modes=["mensajeria", "empleados"],
            required_certs=[], payment_terms_days=30,
        )
        db.add(company)
        db.flush()

    # ── Transport company + vehículo del conductor demo ─────────────────────
    transport_co = db.execute(
        select(TransportCompany).where(TransportCompany.name == "Rapid Courier Orlando LLC")
    ).scalar_one_or_none()
    if not transport_co:
        transport_co = TransportCompany(
            id=uuid.uuid4(), name="Rapid Courier Orlando LLC",
            is_verified=True, is_active=True,
            service_modes=["mensajeria", "empleados"],
            city="Orlando", state="FL", avg_rating=4.9,
        )
        db.add(transport_co)
        db.flush()

    vehicle = db.execute(
        select(Vehicle).where(Vehicle.driver_id == driver.id, Vehicle.is_active == True)
    ).scalar_one_or_none()
    if vehicle and not vehicle.transport_company_id:
        vehicle.transport_company_id = transport_co.id

    # ── Warehouse de origen ──────────────────────────────────────────────────
    warehouse = db.execute(
        select(Warehouse).where(Warehouse.company_id == company.id, Warehouse.code == "NYC-01")
    ).scalar_one_or_none()
    if not warehouse:
        warehouse = Warehouse(
            id=uuid.uuid4(), company_id=company.id, code="NYC-01",
            name="Oficina Principal NYC", address_line1="1221 6th Ave",
            city="New York", state="NY", docks_count=1, is_origin_default=True,
        )
        db.add(warehouse)
        db.flush()

    # ── RUTA 1 — Mensajería con detalle de envío completo ───────────────────
    header1 = RouteHeader(
        id=uuid.uuid4(), company_id=company.id, origin_warehouse_id=warehouse.id,
        transport_company_id=transport_co.id, vehicle_id=vehicle.id if vehicle else None,
        route_number="ORL-2026-000001", title="Ruta Bufetes Legales — Manhattan",
        service_mode="mensajeria", status="published", source="manual",
        scheduled_date=now, scheduled_start=now.replace(hour=10, minute=0),
        scheduled_end=now.replace(hour=13, minute=30),
        gross_pay=87.50, muevo_commission_pct=5.00, muevo_commission_amt=4.38,
        fuel_cost_estimated=5.20, wear_cost_estimated=3.68,
        net_pay_estimated=72.32, margin_pct=83,
        driver_notes="Sobres confidenciales. Requiere firma del receptor en cada parada.",
    )
    db.add(header1)
    db.flush()

    stops1_data = [
        dict(seq=1, company="Sullivan & Cromwell LLP", addr="1221 6th Ave", contact="Maria Chen",
             phone="+1 212 558 4000", notes="Recepción piso 38. Preguntar por Maria Chen.",
             eta=now.replace(hour=10, minute=15), dist=2.1, pod="signature",
             pkg_code="LC-2024-001847", weight=4.5, value=120.00),
        dict(seq=2, company="Skadden Arps", addr="200 Park Ave", contact="James Rodriguez",
             phone="+1 212 735 3000", notes="Urgente. Documentos para firma notarial antes de 12pm.",
             eta=now.replace(hour=10, minute=45), dist=1.8, pod="both",
             pkg_code="LC-2024-001848", weight=2.0, value=80.00),
        dict(seq=3, company="Davis Polk & Wardwell", addr="30 Rockefeller Plaza", contact="Sarah Kim",
             phone="+1 212 450 4000", notes="Entregar en mailroom del piso 45.",
             eta=now.replace(hour=11, minute=20), dist=0.9, pod="photo",
             pkg_code="LC-2024-001849", weight=1.5, value=60.00),
        dict(seq=4, company="Cravath Swaine & Moore", addr="450 Lexington Ave", contact="Tom Walsh",
             phone="+1 212 474 1000", notes="Piso 28. Acceso con badge del edificio.",
             eta=now.replace(hour=12, minute=0), dist=2.4, pod="signature",
             pkg_code="LC-2024-001850", weight=3.0, value=95.00),
    ]

    for s in stops1_data:
        detail = RouteDetail(
            id=uuid.uuid4(), route_header_id=header1.id, sequence_order=s["seq"],
            status="pending", address_line1=s["addr"], city="New York", state="NY",
            company_name=s["company"], contact_name=s["contact"], contact_phone=s["phone"],
            access_notes=s["notes"], eta_scheduled=s["eta"], distance_from_prev_km=s["dist"],
            pod_required=s["pod"],
        )
        db.add(detail)
        db.flush()

        item = ShipmentItem(
            id=uuid.uuid4(), route_detail_id=detail.id, line_number=1,
            package_code=s["pkg_code"], description="Sobre de documentos legales",
            item_type="envelope", quantity=1, weight_lbs=s["weight"],
            declared_value=s["value"], requires_signature=True,
        )
        db.add(item)

    # ── RUTA 2 — Transporte de empleados (sin shipment items, modo empleados) ─
    header2 = RouteHeader(
        id=uuid.uuid4(), company_id=company.id, origin_warehouse_id=warehouse.id,
        transport_company_id=transport_co.id, vehicle_id=vehicle.id if vehicle else None,
        route_number="ORL-2026-000002", title="Transporte Empleados — Brooklyn Tech Hub",
        service_mode="empleados", status="published", source="manual",
        scheduled_date=now, scheduled_start=now.replace(hour=8, minute=0),
        scheduled_end=now.replace(hour=9, minute=30),
        gross_pay=120.00, muevo_commission_pct=5.00, muevo_commission_amt=6.00,
        fuel_cost_estimated=7.80, wear_cost_estimated=4.52,
        net_pay_estimated=95.48, margin_pct=80,
        driver_notes="Ruta puntual. Penalización por retraso en punto de control.",
    )
    db.add(header2)
    db.flush()

    stops2_data = [
        dict(seq=1, company="TechCorp — Pickup A", addr="125 Atlantic Ave", contact="3 empleados",
             notes="Pickup frente al Starbucks. 8:00 AM exacto.",
             eta=now.replace(hour=8, minute=0), dist=0, passengers=3),
        dict(seq=2, company="TechCorp — Pickup B", addr="345 Adams St", contact="2 empleados",
             notes="Frente al edificio rojo.",
             eta=now.replace(hour=8, minute=15), dist=1.2, passengers=2),
        dict(seq=3, company="TechCorp HQ", addr="55 Water St", contact="Destino final",
             phone="+1 212 510 5000", notes="Entrada por puerta sur. Seguridad revisa ID.",
             eta=now.replace(hour=9, minute=0), dist=12.4, passengers=0),
    ]
    for s in stops2_data:
        detail = RouteDetail(
            id=uuid.uuid4(), route_header_id=header2.id, sequence_order=s["seq"],
            status="pending", address_line1=s["addr"], city="Brooklyn" if s["seq"] < 3 else "New York",
            state="NY", company_name=s["company"], contact_name=s["contact"],
            contact_phone=s.get("phone"), access_notes=s["notes"], eta_scheduled=s["eta"],
            distance_from_prev_km=s["dist"], pod_required="signature",
            passengers_count=s["passengers"],
        )
        db.add(detail)

    # Vincular carlos@muevo.app como admin de la company (para el portal warehouse)
    carlos_user = db.execute(
        select(User).where(User.email == "carlos@muevo.app")
    ).scalar_one_or_none()
    if carlos_user:
        existing_admin = db.execute(
            select(CompanyAdmin).where(CompanyAdmin.user_id == carlos_user.id)
        ).scalar_one_or_none()
        if not existing_admin:
            db.add(CompanyAdmin(
                id=uuid.uuid4(), company_id=company.id, user_id=carlos_user.id,
                is_primary=True, can_dispatch=True, can_invoice=True,
            ))

    # Vincular transport@muevo.app como admin de la empresa de transporte
    transport_user = db.execute(
        select(User).where(User.email == "transport@muevo.app")
    ).scalar_one_or_none()
    if transport_user:
        existing_tc_admin = db.execute(
            select(TransportCompanyAdmin).where(TransportCompanyAdmin.user_id == transport_user.id)
        ).scalar_one_or_none()
        if not existing_tc_admin:
            db.add(TransportCompanyAdmin(
                id=uuid.uuid4(), transport_company_id=transport_co.id, user_id=transport_user.id,
                is_primary=True, can_accept_routes=True, can_manage_fleet=True, can_view_billing=True,
            ))

    # Envolver rutas demo en sus propios lotes
    for rh in [header1, header2]:
        existing_item = db.execute(
            select(RouteBatchItem).where(RouteBatchItem.route_header_id == rh.id)
        ).scalar_one_or_none()
        if not existing_item:
            b = RouteBatch(
                id=uuid.uuid4(), company_id=company.id,
                status=BatchStatus.DRAFT, notes=rh.title,
            )
            db.add(b); db.flush()
            db.add(RouteBatchItem(
                id=uuid.uuid4(), batch_id=b.id,
                route_header_id=rh.id, status=BatchItemStatus.PENDING,
            ))

    db.commit()
    return {
        "status": "ok",
        "holding": holding.name,
        "company": company.name,
        "transport_company": transport_co.name,
        "routes_created": 2,
        "routes": ["ORL-2026-000001", "ORL-2026-000002"],
        "warehouse_admin_linked": carlos_user is not None,
        "transport_admin_linked": transport_user is not None,
    }



# ─── Warehouse Portal (cliente corporativo / company admin) ──────────────────

def get_current_company(
    current_user: User,
    db:           Session,
    company_id_header: Optional[str] = None,
) -> "Company":
    """
    Resuelve la Company activa para el usuario autenticado.

    Prioridad:
    1. HoldingUser + X-Company-ID header (nuevo modelo)
    2. HoldingUser con una sola compañía (auto-selección)
    3. CompanyAdmin legacy (backward compat)
    """
    # ── Nuevo modelo: HoldingUser ─────────────────────────────────────────────
    hu = db.execute(
        select(HoldingUser).where(
            HoldingUser.user_id == current_user.id,
            HoldingUser.is_active == True,
        )
    ).scalar_one_or_none()

    if hu:
        if company_id_header:
            company, _ = resolve_active_company(company_id_header, hu, db)
            return company

        # Super-admin sin header: usar primera compañía del holding
        if hu.is_super_admin:
            company = db.execute(
                select(Company).where(Company.holding_id == hu.holding_id)
            ).scalar_one_or_none()
            if company:
                return company

        # Usuario con acceso a exactamente 1 compañía: auto-seleccionar
        accesses = db.execute(
            select(HoldingUserCompany).where(
                HoldingUserCompany.holding_user_id == hu.id,
                HoldingUserCompany.can_view == True,
            )
        ).scalars().all()
        if len(accesses) == 1:
            company = db.get(Company, accesses[0].company_id)
            if company:
                return company

        raise HTTPException(
            status_code=400,
            detail="Especificá la compañía activa con el header X-Company-ID"
        )

    # ── Legacy: CompanyAdmin ──────────────────────────────────────────────────
    admin = db.execute(
        select(CompanyAdmin).where(CompanyAdmin.user_id == current_user.id)
    ).scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=403, detail="Usuario no tiene acceso a ninguna empresa")
    company = db.get(Company, admin.company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return company


@app.get("/api/v1/warehouse/dashboard")
async def warehouse_dashboard(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """KPIs del dashboard principal del portal warehouse."""
    company = get_current_company(current_user, db, x_company_id)

    headers = db.execute(
        select(RouteHeader).where(RouteHeader.company_id == company.id)
    ).scalars().all()

    active      = [r for r in headers if r.status == RouteStatus.IN_PROGRESS]
    scheduled   = [r for r in headers if r.status in (RouteStatus.PUBLISHED, RouteStatus.ASSIGNED)]
    completed   = [r for r in headers if r.status == RouteStatus.COMPLETED]
    total_net   = sum(float(r.net_pay_estimated or 0) for r in headers)

    open_receptions = db.execute(
        select(func.count(Reception.id)).where(
            Reception.company_id == company.id,
            Reception.status.in_([ReceptionStatus.EXPECTED, ReceptionStatus.IN_PROGRESS]),
        )
    ).scalar_one()

    return {
        "company_name":        company.name,
        "active_routes":       len(active),
        "scheduled_routes":    len(scheduled),
        "completed_routes":    len(completed),
        "total_net_this_period": round(total_net, 2),
        "recent_routes":       [serialize_route_header(r) for r in sorted(headers, key=lambda r: r.scheduled_start, reverse=True)[:10]],
        "max_csv_rows":          company.max_csv_rows or DEFAULT_MAX_CSV_ROWS,
        "max_csv_file_size_mb":  company.max_csv_file_size_mb or DEFAULT_MAX_CSV_FILE_SIZE_MB,
        "open_receptions":       open_receptions,
    }


@app.get("/api/v1/warehouse/routes")
async def warehouse_list_routes(
    status:       Optional[str] = None,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Lista todas las rutas de la company del usuario, sin importar quién las ejecute."""
    company = get_current_company(current_user, db, x_company_id)

    query = select(RouteHeader).where(RouteHeader.company_id == company.id)
    if status:
        query = query.where(RouteHeader.status == RouteStatus(status))

    headers = db.execute(query.order_by(RouteHeader.scheduled_start.desc())).scalars().all()
    return [serialize_route_header(r) for r in headers]


@app.get("/api/v1/warehouse/routes/{route_id}")
async def warehouse_get_route(
    route_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Detalle completo de una ruta — solo si pertenece a la company del usuario."""
    company = get_current_company(current_user, db, x_company_id)
    header  = db.get(RouteHeader, uuid.UUID(route_id))

    if not header or header.company_id != company.id:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")

    return serialize_route_header(header)


@app.get("/api/v1/warehouse/transport-companies")
async def warehouse_list_transport_companies(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Empresas de transporte que han ejecutado rutas para esta company."""
    company = get_current_company(current_user, db, x_company_id)

    transport_ids = db.execute(
        select(RouteHeader.transport_company_id)
        .where(RouteHeader.company_id == company.id)
        .where(RouteHeader.transport_company_id.isnot(None))
        .distinct()
    ).scalars().all()

    companies = []
    for tc_id in transport_ids:
        tc = db.get(TransportCompany, tc_id)
        if tc:
            route_count = db.execute(
                select(func.count(RouteHeader.id))
                .where(RouteHeader.company_id == company.id)
                .where(RouteHeader.transport_company_id == tc_id)
            ).scalar()
            companies.append({
                "id": str(tc.id), "name": tc.name, "is_verified": tc.is_verified,
                "avg_rating": float(tc.avg_rating or 0), "on_time_pct": float(tc.on_time_pct or 0),
                "routes_with_us": route_count,
            })
    return companies


@app.get("/api/v1/warehouse/routes/{route_id}/tracking")
async def warehouse_route_tracking(
    route_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """
    Snapshot de la posición actual del conductor para una ruta — usado por
    warehouse.html al cargar la pantalla de tracking, antes de que llegue
    la primera actualización por WebSocket.
    """
    company = get_current_company(current_user, db, x_company_id)
    header  = db.get(RouteHeader, uuid.UUID(route_id))

    if not header or header.company_id != company.id:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")

    driver = None
    if header.vehicle_id:
        vehicle = db.get(Vehicle, header.vehicle_id)
        if vehicle and vehicle.driver_id:
            driver = db.get(Driver, vehicle.driver_id)

    return {
        "route_id": str(header.id),
        "route_number": header.route_number,
        "status": header.status.value if hasattr(header.status, 'value') else header.status,
        "lat": driver.current_lat if driver else None,
        "lng": driver.current_lng if driver else None,
        "last_location_at": driver.last_location_at.isoformat() if driver and driver.last_location_at else None,
        "ws_channel": str(company.id),
    }



# ─── Transport Company Portal (empresa de transporte) ────────────────────────

def get_current_transport_company(current_user: User, db: Session) -> "TransportCompany":
    """Resuelve la TransportCompany que administra el usuario autenticado."""
    admin = db.execute(
        select(TransportCompanyAdmin).where(TransportCompanyAdmin.user_id == current_user.id)
    ).scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=403, detail="Usuario no es administrador de ninguna empresa de transporte")
    tc = db.get(TransportCompany, admin.transport_company_id)
    if not tc:
        raise HTTPException(status_code=404, detail="Empresa de transporte no encontrada")
    return tc


@app.get("/api/v1/transport/dashboard")
async def transport_dashboard(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """KPIs del dashboard del portal de la empresa de transporte."""
    tc = get_current_transport_company(current_user, db)

    # Rutas ofrecidas (asignadas a esta TC pero sin vehículo todavía = pendiente de aceptar)
    offered = db.execute(
        select(RouteHeader)
        .where(RouteHeader.transport_company_id == tc.id)
        .where(RouteHeader.status == RouteStatus.PUBLISHED)
        .where(RouteHeader.vehicle_id.is_(None))
    ).scalars().all()

    active = db.execute(
        select(RouteHeader)
        .where(RouteHeader.transport_company_id == tc.id)
        .where(RouteHeader.status == RouteStatus.IN_PROGRESS)
    ).scalars().all()

    completed_today = db.execute(
        select(RouteHeader)
        .where(RouteHeader.transport_company_id == tc.id)
        .where(RouteHeader.status == RouteStatus.COMPLETED)
    ).scalars().all()

    fleet_size = db.execute(
        select(func.count(Vehicle.id)).where(Vehicle.transport_company_id == tc.id)
    ).scalar()

    return {
        "company_name":      tc.name,
        "is_verified":       tc.is_verified,
        "avg_rating":        float(tc.avg_rating or 0),
        "on_time_pct":       float(tc.on_time_pct or 0),
        "offered_routes":    len(offered),
        "active_routes":     len(active),
        "completed_routes":  len(completed_today),
        "fleet_size":        fleet_size,
        "total_earnings_net": float(tc.total_earnings_net or 0),
        "offered_routes_preview": [serialize_route_header(r) for r in offered[:10]],
    }


@app.get("/api/v1/transport/routes")
async def transport_list_routes(
    status:       Optional[str] = None,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Lista todas las rutas asociadas a la empresa de transporte (ofrecidas, activas, o completadas)."""
    tc = get_current_transport_company(current_user, db)

    query = select(RouteHeader).where(RouteHeader.transport_company_id == tc.id)
    if status:
        query = query.where(RouteHeader.status == RouteStatus(status))

    headers = db.execute(query.order_by(RouteHeader.scheduled_start.desc())).scalars().all()
    return [serialize_route_header(r) for r in headers]


@app.post("/api/v1/transport/routes/{route_id}/accept")
async def transport_accept_route(
    route_id:     str,
    vehicle_id:   Optional[str] = None,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    La empresa de transporte acepta una ruta que el dispatcher le ofreció.
    Si se especifica vehicle_id, lo asigna de inmediato (debe pertenecer a
    esta empresa); si no, la ruta queda aceptada a nivel empresa pero sin
    vehículo concreto todavía (un conductor la tomará desde la app Drive).
    """
    tc = get_current_transport_company(current_user, db)
    header = db.get(RouteHeader, uuid.UUID(route_id))

    if not header or header.transport_company_id != tc.id:
        raise HTTPException(status_code=404, detail="Ruta no encontrada o no asignada a tu empresa")
    if header.status != RouteStatus.PUBLISHED:
        raise HTTPException(status_code=400, detail="La ruta ya no está disponible para aceptar")

    if vehicle_id:
        vehicle = db.get(Vehicle, uuid.UUID(vehicle_id))
        if not vehicle or vehicle.transport_company_id != tc.id:
            raise HTTPException(status_code=400, detail="El vehículo no pertenece a tu empresa")
        header.vehicle_id = vehicle.id
        header.status = RouteStatus.ASSIGNED

    db.commit()
    return {"status": "accepted", "route_id": str(header.id), "route_status": header.status.value}


@app.post("/api/v1/transport/routes/{route_id}/reject")
async def transport_reject_route(
    route_id:     str,
    reason:       Optional[str] = None,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    La empresa de transporte rechaza una ruta ofrecida. Libera
    transport_company_id para que el dispatcher la reasigne a otra
    empresa, e incrementa el contador de rechazos (usado por el
    algoritmo de matching para penalizar el rating).

    Si la ruta venía de una negociación ya aprobada por holding, el
    rechazo reabre el marketplace para todos los transportistas en vez
    de dejarla huérfana — el precio/comisión que se habían fijado quedan
    revertidos, y cualquier empresa (incluida la que acaba de rechazar)
    puede volver a pujar.
    """
    tc = get_current_transport_company(current_user, db)
    header = db.get(RouteHeader, uuid.UUID(route_id))

    if not header or header.transport_company_id != tc.id:
        raise HTTPException(status_code=404, detail="Ruta no encontrada o no asignada a tu empresa")
    if header.status != RouteStatus.PUBLISHED:
        raise HTTPException(status_code=400, detail="La ruta ya no está disponible para rechazar")

    was_negotiated = header.negotiation_status == NegotiationStatus.ACCEPTED

    header.transport_company_id = None
    tc.rejected_routes = (tc.rejected_routes or 0) + 1
    if header.internal_notes:
        header.internal_notes += f" | Rechazada por {tc.name}: {reason or 'sin motivo especificado'}"
    else:
        header.internal_notes = f"Rechazada por {tc.name}: {reason or 'sin motivo especificado'}"

    if was_negotiated:
        header.internal_notes      += " (negociación reabierta a todos los transportistas)"
        header.negotiation_status   = NegotiationStatus.SUGGESTED
        header.status               = RouteStatus.DRAFT
        header.holding_approved_by  = None
        header.holding_approved_at  = None
        header.pending_offer_id     = None
        header.gross_pay            = 0
        header.muevo_commission_amt = 0
        header.net_pay_estimated    = None

    db.commit()
    return {"status": "rejected", "route_id": str(header.id), "reopened_negotiation": was_negotiated}


@app.get("/api/v1/transport/fleet")
async def transport_list_fleet(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Lista la flota de vehículos de la empresa, con su conductor asignado (si tiene)."""
    tc = get_current_transport_company(current_user, db)

    vehicles = db.execute(
        select(Vehicle).where(Vehicle.transport_company_id == tc.id)
    ).scalars().all()

    result = []
    for v in vehicles:
        driver_name, driver_rating, driver_online = None, None, False
        if v.driver_id:
            driver = db.get(Driver, v.driver_id)
            if driver:
                user = db.get(User, driver.user_id)
                driver_name = user.full_name if user else None
                driver_rating = float(driver.avg_rating or 0)
                driver_online = driver.is_online

        active_route = db.execute(
            select(RouteHeader)
            .where(RouteHeader.vehicle_id == v.id)
            .where(RouteHeader.status == RouteStatus.IN_PROGRESS)
        ).scalar_one_or_none()

        result.append({
            "id": str(v.id), "plate": v.plate, "make": v.make, "model": v.model,
            "year": v.year, "vehicle_type": v.vehicle_type.value if hasattr(v.vehicle_type, 'value') else v.vehicle_type,
            "is_active": v.is_active,
            "driver_name": driver_name, "driver_rating": driver_rating, "driver_online": driver_online,
            "active_route_id": str(active_route.id) if active_route else None,
            "active_route_number": active_route.route_number if active_route else None,
        })
    return result


class VehicleCreate(BaseModel):
    plate:           str
    make:            str
    model:           str
    year:            int
    color:           str = "Blanco"
    vehicle_type:    str  # moto | sedan | furgoneta | furgon
    payload_kg:      Optional[float] = None
    volume_m3:       Optional[float] = None
    passenger_seats: int = 2
    fuel_type:       str = "gasoline"


@app.post("/api/v1/transport/fleet", status_code=201)
async def transport_add_vehicle(
    data:         VehicleCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Agrega un vehículo nuevo a la flota de la empresa de transporte."""
    tc = get_current_transport_company(current_user, db)

    existing = db.execute(
        select(Vehicle).where(Vehicle.plate == data.plate)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"Ya existe un vehículo con placa {data.plate}")

    valid_types = [t.value for t in VehicleType]
    vtype = data.vehicle_type if data.vehicle_type in valid_types else "furgoneta"

    vehicle = Vehicle(
        id=uuid.uuid4(), transport_company_id=tc.id, driver_id=None, is_active=True,
        vehicle_type=vtype, make=data.make, model=data.model, year=data.year, color=data.color,
        plate=data.plate, payload_kg=data.payload_kg, volume_m3=data.volume_m3,
        passenger_seats=data.passenger_seats, fuel_type=data.fuel_type,
    )
    db.add(vehicle)
    db.commit()

    return {"status": "created", "vehicle_id": str(vehicle.id), "plate": vehicle.plate}


@app.get("/api/v1/transport/fleet/tracking")
async def transport_fleet_tracking(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Snapshot de posición de todos los vehículos de la flota con ruta en
    curso — usado por transport.html para inicializar el mapa de flota
    antes de que lleguen actualizaciones por WebSocket.
    """
    tc = get_current_transport_company(current_user, db)

    vehicles = db.execute(
        select(Vehicle).where(Vehicle.transport_company_id == tc.id).where(Vehicle.driver_id.isnot(None))
    ).scalars().all()

    result = []
    for v in vehicles:
        driver = db.get(Driver, v.driver_id) if v.driver_id else None
        if not driver or driver.current_lat is None:
            continue
        active_route = db.execute(
            select(RouteHeader)
            .where(RouteHeader.vehicle_id == v.id)
            .where(RouteHeader.status == RouteStatus.IN_PROGRESS)
        ).scalar_one_or_none()
        result.append({
            "vehicle_id": str(v.id), "plate": v.plate,
            "lat": driver.current_lat, "lng": driver.current_lng,
            "last_location_at": driver.last_location_at.isoformat() if driver.last_location_at else None,
            "route_id": str(active_route.id) if active_route else None,
            "route_number": active_route.route_number if active_route else None,
        })

    return {"vehicles": result, "ws_channel": f"fleet-{tc.id}"}


@app.get("/api/v1/transport/billing")
async def transport_billing(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Facturación consolidada de la empresa de transporte — rutas completadas con su desglose de pago."""
    tc = get_current_transport_company(current_user, db)

    headers = db.execute(
        select(RouteHeader)
        .where(RouteHeader.transport_company_id == tc.id)
        .where(RouteHeader.status == RouteStatus.COMPLETED)
        .order_by(RouteHeader.scheduled_start.desc())
    ).scalars().all()

    payouts = []
    for r in headers:
        payouts.append({
            "route_id": str(r.id), "route_number": r.route_number, "title": r.title,
            "date": r.scheduled_date.isoformat() if r.scheduled_date else None,
            "client_company": r.company.name if r.company else None,
            "gross_pay": float(r.gross_pay or 0),
            "commission": float(r.muevo_commission_amt or 0),
            "net_pay": float(r.net_pay_actual or r.net_pay_estimated or 0),
        })

    total_net = sum(p["net_pay"] for p in payouts)

    return {
        "total_net_earned": round(total_net, 2),
        "payout_count": len(payouts),
        "payouts": payouts,
    }


@app.get("/api/v1/warehouse/billing")
async def warehouse_billing(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """Historial de facturación — rutas completadas con su desglose de costos."""
    company = get_current_company(current_user, db, x_company_id)

    headers = db.execute(
        select(RouteHeader)
        .where(RouteHeader.company_id == company.id)
        .where(RouteHeader.status == RouteStatus.COMPLETED)
        .order_by(RouteHeader.scheduled_start.desc())
    ).scalars().all()

    invoices = []
    for r in headers:
        invoices.append({
            "route_id": str(r.id), "route_number": r.route_number, "title": r.title,
            "date": r.scheduled_date.isoformat() if r.scheduled_date else None,
            "transport_company": r.transport_company.name if r.transport_company else None,
            "gross_pay": float(r.gross_pay or 0),
            "commission": float(r.muevo_commission_amt or 0),
            "net_pay": float(r.net_pay_actual or r.net_pay_estimated or 0),
        })

    total_spend     = sum(i["gross_pay"] for i in invoices)
    total_commission = sum(i["commission"] for i in invoices)

    return {
        "total_spend": round(total_spend, 2),
        "total_commission": round(total_commission, 2),
        "invoice_count": len(invoices),
        "invoices": invoices,
    }


class CsvRouteUpload(BaseModel):
    """Una ruta individual dentro de un upload CSV/bulk del portal warehouse."""
    title: str
    service_mode: str  # mensajeria | logistica | empleados
    scheduled_date: str  # ISO date
    scheduled_start: str  # ISO datetime
    scheduled_end: str  # ISO datetime
    gross_pay: float
    origin_warehouse_id: Optional[str] = None
    required_vehicle_type: Optional[str] = None
    stops: List[dict] = []  # [{address, city, state, company_name, contact_name, packages_count, weight_lbs, ...}]


class CsvBulkUpload(BaseModel):
    routes: List[CsvRouteUpload]


@app.post("/api/v1/warehouse/routes/bulk-import", status_code=201)
async def warehouse_bulk_import_routes(
    data:         CsvBulkUpload,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
    x_company_id: Optional[str] = Header(None),
):
    """
    Importa N rutas desde el flujo de upload CSV del portal warehouse.
    Crea cada RouteHeader en estado 'draft' junto con sus RouteDetails.
    El despacho automático (asignación de transport_company) es un paso
    posterior, no incluido en este endpoint.
    """
    company = get_current_company(current_user, db, x_company_id)
    created = []

    for route_data in data.routes:
        gross = route_data.gross_pay
        commission_pct = 5.00
        commission_amt = round(gross * commission_pct / 100, 2)

        # Generar route_number único
        route_number = f"ORL-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        header = RouteHeader(
            id=uuid.uuid4(), company_id=company.id,
            origin_warehouse_id=uuid.UUID(route_data.origin_warehouse_id) if route_data.origin_warehouse_id else None,
            route_number=route_number, title=route_data.title,
            service_mode=route_data.service_mode, status="draft", source="csv_upload",
            scheduled_date=datetime.fromisoformat(route_data.scheduled_date),
            scheduled_start=datetime.fromisoformat(route_data.scheduled_start),
            scheduled_end=datetime.fromisoformat(route_data.scheduled_end),
            required_vehicle_type=route_data.required_vehicle_type,
            gross_pay=gross, muevo_commission_pct=commission_pct, muevo_commission_amt=commission_amt,
            net_pay_estimated=round(gross - commission_amt, 2),
        )
        db.add(header)
        db.flush()

        for i, stop in enumerate(route_data.stops):
            detail = RouteDetail(
                id=uuid.uuid4(), route_header_id=header.id, sequence_order=i + 1,
                status="pending",
                address_line1=stop.get("address", ""), city=stop.get("city", ""),
                state=stop.get("state", ""), company_name=stop.get("company_name"),
                contact_name=stop.get("contact_name"), contact_phone=stop.get("contact_phone"),
                packages_count=stop.get("packages_count", 0), weight_lbs=stop.get("weight_lbs", 0),
                volume_ft3=stop.get("volume_ft3", 0), cargo_value=stop.get("cargo_value", 0),
                is_fragile=stop.get("is_fragile", False), is_hazmat=stop.get("is_hazmat", False),
                dock_number=stop.get("dock_number"),
                pod_required=stop.get("pod_required", "signature"),
                passengers_count=stop.get("passengers_count", 0),
            )
            db.add(detail)

        # Cada ruta importada por CSV vive en su propio lote
        batch = RouteBatch(
            id=uuid.uuid4(),
            company_id=company.id,
            batch_number=next_batch_number(company.id, db),
            status=BatchStatus.DRAFT,
            notes=header.title,
        )
        db.add(batch)
        db.flush()
        db.add(RouteBatchItem(
            id=uuid.uuid4(),
            batch_id=batch.id,
            route_header_id=header.id,
            status=BatchItemStatus.PENDING,
        ))
        created.append(header)

    db.commit()

    return {
        "status": "ok",
        "routes_created": len(created),
        "route_numbers": [r.route_number for r in created],
    }


# ─── Push Notifications ───────────────────────────────────────────────────────

class PushTokenUpdate(BaseModel):
    push_token: str


@app.post("/api/v1/drivers/me/push-token")
async def save_push_token(
    data:         PushTokenUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Guarda el Expo push token del conductor."""
    driver = db.execute(
        select(Driver).where(Driver.user_id == current_user.id)
    ).scalar_one_or_none()

    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Guardar en stripe_account_id temporalmente hasta agregar columna push_token
    # En producción agregar columna push_token a la tabla drivers
    driver.stripe_account_id = data.push_token
    db.commit()

    return {"status": "ok", "token_saved": True}


@app.post("/api/v1/routes/{route_id}/notify-driver")
async def notify_driver(
    route_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Envía una notificación push al conductor cuando se le asigna una ruta.
    Usa la Expo Push API para entregar la notificación al dispositivo.
    """
    header = db.get(RouteHeader, uuid.UUID(route_id))
    if not header:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")

    vehicle = db.get(Vehicle, header.vehicle_id) if header.vehicle_id else None
    driver  = db.get(Driver, vehicle.driver_id) if vehicle and vehicle.driver_id else None
    if not driver:
        raise HTTPException(status_code=404, detail="Conductor no encontrado")

    push_token = driver.stripe_account_id
    if not push_token or not push_token.startswith("ExponentPushToken"):
        return {"status": "no_token", "message": "Conductor no tiene push token registrado"}

    # Enviar via Expo Push API
    import httpx
    payload = {
        "to":     push_token,
        "sound":  "default",
        "title":  "🚐 Nueva ruta disponible",
        "body":   f"{header.title} · {header.total_stops} paradas · ${float(header.net_pay_estimated or 0):.2f} neto",
        "data":   {
            "route_id":   str(header.id),
            "type":       "new_route",
            "time_start": header.scheduled_start.isoformat() if header.scheduled_start else "",
        },
        "channelId": "muevo-routes",
        "badge":  1,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://exp.host/--/api/v2/push/send",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10.0,
            )
        result = response.json()
        return {"status": "sent", "expo_response": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/v1/dev/test-notification")
async def test_notification(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Envía una notificación de prueba al conductor autenticado."""
    driver = db.execute(
        select(Driver).where(Driver.user_id == current_user.id)
    ).scalar_one_or_none()

    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    push_token = driver.stripe_account_id
    if not push_token or not push_token.startswith("ExponentPushToken"):
        return {
            "status":  "no_token",
            "message": "Registrá el dispositivo primero desde la app",
        }

    import httpx
    payload = {
        "to":    push_token,
        "sound": "default",
        "title": "🚐 Nueva ruta disponible",
        "body":  "Bufetes Legales — Manhattan · 4 paradas · $72.32 neto",
        "data":  {"type": "test", "route_id": "R-TEST-001"},
        "badge": 1,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://exp.host/--/api/v2/push/send",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10.0,
            )
        return {"status": "sent", "response": response.json()}
    except Exception as e:
        return {"status": "error", "message": str(e)}
