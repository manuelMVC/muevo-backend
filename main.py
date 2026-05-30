"""
main.py — Muevo Backend (FastAPI)
==================================
Servidor principal. Levantá con:
    uvicorn main:app --reload --port 8000

Endpoints incluidos:
  POST /api/v1/rides/estimate  — precio real desde pricing.py
  POST /api/v1/rides/request   — crear viaje
  GET  /api/v1/rides/{id}/status
  POST /api/v1/auth/token      — login JWT
  POST /api/v1/auth/register
  GET  /health
"""

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
import jwt
import uuid

# Importar el servicio de pricing que ya generamos
from pricing import (
    calculate_trip_pricing,
    RidePricingRequest,
    DriverVehicleInfo,
    ServiceType,
    Extra,
)

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Muevo API",
    description="Backend para la app de movilidad Muevo",
    version="1.0.0",
)

# CORS — permite que el frontend en localhost:8081 llame al backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8081",
        "http://localhost:3000",
        "http://127.0.0.1:8081",
        "exp://localhost:8081",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Config ───────────────────────────────────────────────────────────────────

SECRET_KEY = "muevo-secret-key-cambiar-en-produccion"
ALGORITHM  = "HS256"
TOKEN_EXPIRE_HOURS = 24

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

# Base de datos en memoria (reemplazar con PostgreSQL en producción)
USERS_DB: dict = {
    "carlos@muevo.app": {
        "id": "user-001",
        "email": "carlos@muevo.app",
        "password": "muevo123",   # En producción: bcrypt hash
        "full_name": "Carlos Rodriguez",
        "avatar_initials": "CR",
        "wallet_balance": 24.50,
        "role": "passenger",
    },
    "driver@muevo.app": {
        "id": "driver-001",
        "email": "driver@muevo.app",
        "password": "muevo123",
        "full_name": "Carlos Mendez",
        "avatar_initials": "CM",
        "wallet_balance": 148.20,
        "role": "driver",
    },
}

RIDES_DB: dict = {}

# ─── Modelos Pydantic ─────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    full_name: str
    email: str
    password: str
    role: str = "passenger"

class EstimateRequest(BaseModel):
    service_type: str
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float
    extras: list[str] = []
    demand_multiplier: float = 1.0
    preferred_language: str = "any"
    # Info del vehículo (para conductores)
    vehicle_type: str = "sedan"
    vehicle_mpg: float = 28.0
    vehicle_odometer: int = 0
    city_state: str = "New York, NY"

class RideRequest(EstimateRequest):
    payment_method: str = "card"

# ─── Helpers JWT ──────────────────────────────────────────────────────────────

def create_token(user_id: str, email: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": user_id, "email": email, "exp": expire},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )

def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("email")
        user = next((u for u in USERS_DB.values() if u["email"] == email), None)
        if not user:
            raise HTTPException(status_code=401, detail="Usuario no encontrado")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido")

def haversine_miles(lat1, lng1, lat2, lng2) -> float:
    """Distancia en millas entre dos coordenadas GPS."""
    from math import radians, cos, sin, asin, sqrt
    R = 3958.8  # radio de la Tierra en millas
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlng/2)**2
    return 2 * R * asin(sqrt(a))

# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "app": "Muevo"}

# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/api/v1/auth/token")
async def login(form: OAuth2PasswordRequestForm = Depends()):
    user = USERS_DB.get(form.username)
    if not user or user["password"] != form.password:
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    token = create_token(user["id"], user["email"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id":               user["id"],
            "name":             user["full_name"],
            "email":            user["email"],
            "avatar_initials":  user["avatar_initials"],
            "wallet_balance":   user["wallet_balance"],
            "role":             user["role"],
        },
    }

@app.post("/api/v1/auth/register", status_code=201)
async def register(data: RegisterRequest):
    if data.email in USERS_DB:
        raise HTTPException(status_code=400, detail="El email ya está registrado")
    user_id = f"user-{uuid.uuid4().hex[:8]}"
    initials = "".join(w[0].upper() for w in data.full_name.split()[:2])
    USERS_DB[data.email] = {
        "id":               user_id,
        "email":            data.email,
        "password":         data.password,
        "full_name":        data.full_name,
        "avatar_initials":  initials,
        "wallet_balance":   0.0,
        "role":             data.role,
    }
    return {"id": user_id, "email": data.email, "name": data.full_name}

# ─── Rides ────────────────────────────────────────────────────────────────────

@app.post("/api/v1/rides/estimate")
async def estimate_ride(data: EstimateRequest):
    """
    Calcula el precio real del viaje usando pricing.py.
    Devuelve el desglose completo para el conductor y la tarifa al pasajero.
    """
    # Calcular distancia real desde coordenadas GPS
    distance_miles = haversine_miles(
        data.origin_lat, data.origin_lng,
        data.dest_lat,   data.dest_lng,
    )
    # Estimación de duración (velocidad promedio NYC: 15 mph)
    avg_speed_mph  = 15.0
    duration_minutes = (distance_miles / avg_speed_mph) * 60

    # Mapear extras del string al enum
    extras_map = {
        "luggage":  Extra.EXTRA_LUGGAGE,
        "child":    Extra.CHILD_SEAT,
        "silent":   Extra.SILENT_RIDE,
        "lock":     Extra.PRICE_LOCK,
        "wheel":    Extra.WHEELCHAIR,
        "senior":   Extra.SENIOR_ASSIST,
        "round":    Extra.ROUND_TRIP,
        "schedule": Extra.SCHEDULED_RIDE,
    }
    parsed_extras = [extras_map[e] for e in data.extras if e in extras_map]

    # Mapear tipo de servicio
    service_map = {
        "ride":     ServiceType.RIDE_STANDARD,
        "ride_xl":  ServiceType.RIDE_XL,
        "premium":  ServiceType.RIDE_PREMIUM,
        "eco":      ServiceType.RIDE_ECO,
        "airport":  ServiceType.RIDE_AIRPORT,
        "food":     ServiceType.FOOD_DELIVERY,
        "package":  ServiceType.PACKAGE,
        "petride":  ServiceType.PET_RIDE,
    }
    service_type = service_map.get(data.service_type, ServiceType.RIDE_STANDARD)

    # Construir request para pricing.py
    pricing_request = RidePricingRequest(
        service_type      = service_type,
        distance_miles    = max(distance_miles, 0.5),
        duration_minutes  = max(duration_minutes, 2.0),
        deadhead_miles    = 0.8,
        driver_vehicle    = DriverVehicleInfo(
            vehicle_type = data.vehicle_type,
            mpg          = data.vehicle_mpg,
            odometer     = data.vehicle_odometer,
        ),
        extras               = parsed_extras,
        demand_multiplier    = data.demand_multiplier,
        platform_commission  = 0.10,   # 10% — Fase 1 Muevo
        city_state           = data.city_state,
    )

    # Calcular con pricing.py
    result = await calculate_trip_pricing(pricing_request)

    # ETA estimado (conductor más cercano: ~3-8 min promedio)
    eta_minutes = {
        "ride": 4, "ride_xl": 6, "premium": 8, "eco": 5,
        "airport": 5, "food": 18, "package": 12, "petride": 7,
    }.get(data.service_type, 5)

    return {
        # Para el pasajero
        "gross_fare":       float(result.fare.total_fare),
        "subtotal":         float(result.fare.subtotal),
        "extras_total":     float(result.fare.extras_total),
        "booking_fee":      float(result.fare.booking_fee),
        "surge_multiplier": float(result.fare.surge_multiplier),

        # Para el conductor (transparencia total)
        "driver_net":       float(result.driver_net),
        "platform_fee":     float(result.costs.platform_fee),
        "fuel_cost":        float(result.costs.fuel_cost),
        "wear_cost":        float(result.costs.wear_cost),
        "margin_pct":       float(result.margin_pct),
        "net_per_mile":     float(result.net_per_mile),
        "profitability":    result.profitability.value,
        "ai_recommendation": result.ai_recommendation,

        # Metadatos
        "distance_miles":   round(distance_miles, 2),
        "duration_minutes": round(duration_minutes, 1),
        "eta_minutes":      eta_minutes,
        "gas_price_used":   float(result.gas_price_used),
        "service_type":     data.service_type,
    }


@app.post("/api/v1/rides/request", status_code=201)
async def request_ride(
    data: RideRequest,
    current_user: dict = Depends(get_current_user),
):
    """Crea un viaje y asigna un conductor disponible."""
    ride_id = f"ride-{uuid.uuid4().hex[:8]}"

    # Obtener el precio estimado
    estimate = await estimate_ride(data)

    ride = {
        "id":                  ride_id,
        "passenger_id":        current_user["id"],
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
        "pricing": {
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
            "vehicle_type":     "sedan",
            "mpg":              32,
            "preferred_language": "es",
            "is_bilingual":     True,
            "current_location": {"latitude": data.origin_lat + 0.01, "longitude": data.origin_lng + 0.01},
            "avatar_initials":  "CM",
        },
        "created_at": datetime.utcnow().isoformat(),
    }

    RIDES_DB[ride_id] = ride
    return ride


@app.get("/api/v1/rides/{ride_id}/status")
async def get_ride_status(
    ride_id: str,
    current_user: dict = Depends(get_current_user),
):
    ride = RIDES_DB.get(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Viaje no encontrado")
    return ride


@app.get("/api/v1/drivers/me/earnings")
async def get_earnings(
    period: str = "today",
    current_user: dict = Depends(get_current_user),
):
    return {
        "gross":   148.20,
        "net":     116.80,
        "trips":   12,
        "rating":  4.93,
        "hours":   4.8,
        "per_hour": 24.35,
    }
