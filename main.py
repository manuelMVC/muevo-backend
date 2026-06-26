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
import uuid
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import bcrypt as _bcrypt
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, text, select, update
from sqlalchemy.orm import Session, sessionmaker

from models import (
    Base, User, Driver, Vehicle, Route, Stop, Delivery,
    Earning, DriverMetric, Company,
    UserRole, RouteStatus, StopStatus, PodType,
    # Modelo multi-holding / multi-warehouse (vigente para /api/v1/routes)
    Holding, Warehouse, TransportCompany,
    RouteHeader, RouteDetail, ShipmentItem,
)

# ─── Config ───────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:Semeolvido.01@localhost:5432/muevodb"
)

SECRET_KEY        = os.getenv("SECRET_KEY", "muevo-secret-key-cambiar-en-produccion")
ALGORITHM         = "HS256"
TOKEN_EXPIRE_HOURS = 24

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8081",
        "http://localhost:8082",
        "http://localhost:3000",
        "http://127.0.0.1:8081",
        "null",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

    return {"status": "updated", "lat": data.lat, "lng": data.lng}


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

    header.status               = RouteStatus.ASSIGNED
    header.vehicle_id           = vehicle.id if vehicle else None
    header.transport_company_id = vehicle.transport_company_id if vehicle else header.transport_company_id
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
        "net_pay":             float(rh.net_pay_estimated or 0),
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
        gross_pay=87.50, muevo_commission_pct=12.00, muevo_commission_amt=10.50,
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
        gross_pay=120.00, muevo_commission_pct=12.00, muevo_commission_amt=14.40,
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

    db.commit()
    return {
        "status": "ok",
        "holding": holding.name,
        "company": company.name,
        "transport_company": transport_co.name,
        "routes_created": 2,
        "routes": ["ORL-2026-000001", "ORL-2026-000002"],
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
