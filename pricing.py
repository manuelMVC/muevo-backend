"""
pricing.py — Velo Rideshare Platform
=====================================
Servicio de cálculo de costos y precios en tiempo real.

Responsabilidades:
  - Calcular el costo real por viaje para el conductor (gas, desgaste, tiempo muerto)
  - Calcular la tarifa que paga el pasajero (base + por milla + por minuto + surge)
  - Obtener el precio del gas en tiempo real (EIA API)
  - Aplicar surge pricing dinámico según demanda
  - Devolver el desglose completo antes de que el conductor acepte el viaje
  - Soporte para todos los tipos de servicio: Ride, Food, Package, PetRide, Airport

Autor: Velo Engineering
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Optional

import httpx
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Enums y constantes
# ─────────────────────────────────────────────────────────────────────────────

class ServiceType(str, Enum):
    RIDE_STANDARD  = "ride_standard"
    RIDE_XL        = "ride_xl"
    RIDE_PREMIUM   = "ride_premium"
    RIDE_ECO       = "ride_eco"
    RIDE_AIRPORT   = "ride_airport"
    RIDE_HOURLY    = "ride_hourly"
    FOOD_DELIVERY  = "food_delivery"
    PACKAGE        = "package"
    PET_RIDE       = "pet_ride"


class Extra(str, Enum):
    EXTRA_LUGGAGE   = "extra_luggage"
    CHILD_SEAT      = "child_seat"
    SILENT_RIDE     = "silent_ride"
    WHEELCHAIR      = "wheelchair"
    SENIOR_ASSIST   = "senior_assist"
    PRICE_LOCK      = "price_lock"
    ROUND_TRIP      = "round_trip"
    SCHEDULED_RIDE  = "scheduled_ride"


class TripProfitability(str, Enum):
    EXCELLENT = "excellent"   # margen > 70%
    GOOD      = "good"        # margen 50–70%
    MODERATE  = "moderate"    # margen 30–50%
    POOR      = "poor"        # margen < 30%


# ─────────────────────────────────────────────────────────────────────────────
# Tarifas base por tipo de servicio (USD)
# ─────────────────────────────────────────────────────────────────────────────

BASE_FARES: dict[ServiceType, dict] = {
    ServiceType.RIDE_STANDARD: {
        "base":       2.50,
        "per_mile":   1.80,
        "per_minute": 0.35,
        "minimum":    8.00,
    },
    ServiceType.RIDE_XL: {
        "base":       4.00,
        "per_mile":   2.40,
        "per_minute": 0.45,
        "minimum":    14.00,
    },
    ServiceType.RIDE_PREMIUM: {
        "base":       8.00,
        "per_mile":   3.50,
        "per_minute": 0.65,
        "minimum":    22.00,
    },
    ServiceType.RIDE_ECO: {
        "base":       2.00,
        "per_mile":   1.60,
        "per_minute": 0.30,
        "minimum":    7.00,
    },
    ServiceType.RIDE_AIRPORT: {
        # Precio fijo: base + por_milla; sin surge
        "base":       15.00,
        "per_mile":   2.20,
        "per_minute": 0.00,
        "minimum":    28.00,
        "fixed_route": True,
    },
    ServiceType.RIDE_HOURLY: {
        "base":       35.00,   # por hora completa
        "per_mile":   0.50,    # millas adicionales
        "per_minute": 0.00,
        "minimum":    35.00,
    },
    ServiceType.FOOD_DELIVERY: {
        "base":       2.00,
        "per_mile":   1.50,
        "per_minute": 0.25,
        "minimum":    4.00,
    },
    ServiceType.PACKAGE: {
        "base":       3.00,
        "per_mile":   1.70,
        "per_minute": 0.30,
        "minimum":    6.00,
    },
    ServiceType.PET_RIDE: {
        "base":       5.00,
        "per_mile":   2.10,
        "per_minute": 0.40,
        "minimum":    14.00,
    },
}

# Costo adicional por extra (USD, cargo al pasajero)
EXTRA_CHARGES: dict[Extra, float] = {
    Extra.EXTRA_LUGGAGE:  2.00,
    Extra.CHILD_SEAT:     3.00,
    Extra.SILENT_RIDE:    0.00,
    Extra.WHEELCHAIR:     4.00,
    Extra.SENIOR_ASSIST:  0.00,
    Extra.PRICE_LOCK:     0.99,
    Extra.ROUND_TRIP:     0.00,   # se aplica 2× al calcular
    Extra.SCHEDULED_RIDE: 0.00,
}

# Desgaste del vehículo por milla (IRS standard + estimaciones por tipo)
# Fuente: IRS mileage rate 2025 = $0.70/mi; desgaste = 70% de eso
WEAR_COST_PER_MILE: dict[str, float] = {
    "sedan":   0.18,
    "suv":     0.22,
    "van":     0.25,
    "luxury":  0.32,
    "electric": 0.12,   # menor mantenimiento
    "default": 0.18,
}

# Plataforma fee — Fase 1 lanzamiento
DEFAULT_PLATFORM_COMMISSION = Decimal("0.10")   # 10%

# Precio fallback del gas si la API externa falla (USD/galón, promedio EEUU)
GAS_PRICE_FALLBACK_USD = 3.49

# EIA API — U.S. Energy Information Administration (datos semanales de gas prices)
# Registro gratuito en: https://www.eia.gov/opendata/register.php
EIA_API_URL = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"


# ─────────────────────────────────────────────────────────────────────────────
# Modelos de entrada y salida (Pydantic v2)
# ─────────────────────────────────────────────────────────────────────────────

class DriverVehicleInfo(BaseModel):
    """Información del vehículo del conductor para calcular costos reales."""
    vehicle_type:  str   = Field(default="sedan", description="sedan | suv | van | luxury | electric")
    mpg:           float = Field(default=28.0,    description="Millas por galón (o equivalente eléctrico MPGe)")
    year:          int   = Field(default=2020)
    make:          str   = Field(default="Toyota")
    model:         str   = Field(default="Camry")
    odometer:      int   = Field(default=0,       description="Millas acumuladas (para ajustar desgaste)")

    @field_validator("mpg")
    @classmethod
    def mpg_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("MPG debe ser mayor a 0")
        return v


class RidePricingRequest(BaseModel):
    """Request para calcular el precio y costo de un viaje."""
    service_type:       ServiceType
    distance_miles:     float       = Field(..., gt=0, description="Distancia del viaje en millas")
    duration_minutes:   float       = Field(..., gt=0, description="Duración estimada en minutos")
    deadhead_miles:     float       = Field(default=0.5, description="Millas en vacío hasta el pickup")
    driver_vehicle:     DriverVehicleInfo = Field(default_factory=DriverVehicleInfo)
    extras:             list[Extra] = Field(default_factory=list)
    demand_multiplier:  float       = Field(default=1.0, ge=1.0, le=4.0, description="Factor de surge pricing")
    is_airport_fixed:   bool        = Field(default=False)
    hours_requested:    float       = Field(default=1.0, description="Para RIDE_HOURLY")
    platform_commission: float      = Field(default=float(DEFAULT_PLATFORM_COMMISSION), ge=0.05, le=0.40)
    city_state:         str         = Field(default="New York, NY", description="Para precio de gas local")
    bilingual_driver:   bool        = Field(default=False)
    preferred_language: Optional[str] = Field(default=None)


class CostBreakdown(BaseModel):
    """Desglose completo de costos para el conductor."""
    fuel_cost:          Decimal = Field(description="Costo de combustible (USD)")
    wear_cost:          Decimal = Field(description="Desgaste y depreciación del vehículo (USD)")
    deadhead_cost:      Decimal = Field(description="Costo del trayecto en vacío hasta el pickup (USD)")
    platform_fee:       Decimal = Field(description="Comisión de la plataforma (USD)")
    total_costs:        Decimal = Field(description="Suma de todos los costos (USD)")


class FareBreakdown(BaseModel):
    """Desglose completo de la tarifa que paga el pasajero."""
    base_fare:          Decimal
    distance_fare:      Decimal
    time_fare:          Decimal
    surge_multiplier:   Decimal
    extras_total:       Decimal
    subtotal:           Decimal
    booking_fee:        Decimal   = Decimal("1.50")
    total_fare:         Decimal   = Field(description="Total que paga el pasajero")


class TripPricingResult(BaseModel):
    """
    Resultado completo del cálculo de pricing.
    Este es el objeto que se devuelve al conductor ANTES de aceptar el viaje,
    con toda la información para tomar una decisión informada.
    """
    # Tarifa
    fare:               FareBreakdown
    costs:              CostBreakdown

    # Resumen ejecutivo para el conductor
    gross_earnings:     Decimal = Field(description="Lo que paga la plataforma al conductor (tarifa − comisión)")
    driver_net:         Decimal = Field(description="Ganancia neta real (gross − combustible − desgaste)")
    net_per_mile:       Decimal = Field(description="Ganancia neta por milla recorrida")
    net_per_minute:     Decimal = Field(description="Ganancia neta por minuto activo")
    margin_pct:         Decimal = Field(description="Porcentaje de margen neto (driver_net / gross_earnings)")

    # Contexto del mercado
    gas_price_used:     Decimal = Field(description="Precio del gas utilizado (USD/galón)")
    profitability:      TripProfitability
    ai_recommendation:  str     = Field(description="Recomendación del sistema basada en rentabilidad")

    # Metadatos del viaje
    service_type:       ServiceType
    distance_miles:     Decimal
    duration_minutes:   Decimal
    platform_commission_pct: Decimal

    # Bandera de precio bloqueado
    price_locked:       bool    = False


# ─────────────────────────────────────────────────────────────────────────────
# Obtención del precio del gas en tiempo real
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_gas_price(city_state: str = "New York, NY", eia_api_key: Optional[str] = None) -> float:
    """
    Obtiene el precio del gas más reciente desde la API de la EIA.
    Retorna el precio promedio nacional si no hay clave de API o si falla la llamada.

    Args:
        city_state: Ciudad y estado para buscar precio regional.
        eia_api_key: Clave de API de la EIA (configurar en .env como EIA_API_KEY).

    Returns:
        Precio del gas en USD/galón.
    """
    if not eia_api_key:
        logger.warning("EIA_API_KEY no configurada. Usando precio fallback: $%.2f/gal", GAS_PRICE_FALLBACK_USD)
        return GAS_PRICE_FALLBACK_USD

    # Mapeo de regiones EIA por estado
    state_to_eia_series = {
        "NY": "EMM_EPMRR_PTE_Y35NY_DPG",  # New York
        "CA": "EMM_EPMRR_PTE_R50_DPG",    # California
        "TX": "EMM_EPMRR_PTE_Y44TX_DPG",  # Texas
        "FL": "EMM_EPMRR_PTE_Y12FL_DPG",  # Florida
        "IL": "EMM_EPMRR_PTE_Y17IL_DPG",  # Illinois
    }

    # Extraer estado del string "City, ST"
    state_code = city_state.split(",")[-1].strip()[:2].upper()
    series_id  = state_to_eia_series.get(state_code, "EMM_EPMRR_PTE_NUS_DPG")  # fallback: nacional

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                EIA_API_URL,
                params={
                    "api_key": eia_api_key,
                    "frequency": "weekly",
                    "data[0]": "value",
                    "series": series_id,
                    "sort[0][column]": "period",
                    "sort[0][direction]": "desc",
                    "length": 1,
                    "offset": 0,
                },
            )
            response.raise_for_status()
            data = response.json()
            price = float(data["response"]["data"][0]["value"])
            logger.info("Precio del gas obtenido de EIA: $%.3f/gal (%s)", price, state_code)
            return price

    except Exception as exc:
        logger.warning("Error al obtener precio del gas desde EIA: %s. Usando fallback.", exc)
        return GAS_PRICE_FALLBACK_USD


# ─────────────────────────────────────────────────────────────────────────────
# Cálculos auxiliares
# ─────────────────────────────────────────────────────────────────────────────

def _round2(value: float | Decimal) -> Decimal:
    """Redondea a 2 decimales con ROUND_HALF_UP (estándar financiero)."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_fuel_cost(
    total_miles:  float,
    mpg:          float,
    gas_price:    float,
) -> Decimal:
    """
    Calcula el costo de combustible para el trayecto completo.

    Fórmula: (millas / mpg) × precio_galón

    Args:
        total_miles: Total de millas recorridas (viaje + trayecto en vacío).
        mpg:         Millas por galón del vehículo.
        gas_price:   Precio actual del galón en USD.

    Returns:
        Costo de combustible en USD.
    """
    if mpg <= 0:
        raise ValueError("MPG debe ser mayor a 0")

    gallons_used = total_miles / mpg
    cost         = gallons_used * gas_price
    return _round2(cost)


def calculate_wear_cost(
    total_miles:    float,
    vehicle_type:   str = "sedan",
    odometer:       int = 0,
) -> Decimal:
    """
    Calcula el costo de desgaste y depreciación del vehículo.

    El costo base por milla se ajusta según el kilometraje acumulado:
    vehículos con más millas tienen mayor probabilidad de necesitar mantenimiento.

    Args:
        total_miles:  Millas del trayecto completo.
        vehicle_type: Tipo de vehículo (sedan, suv, van, luxury, electric).
        odometer:     Millas acumuladas del vehículo.

    Returns:
        Costo de desgaste en USD.
    """
    base_rate = WEAR_COST_PER_MILE.get(vehicle_type.lower(), WEAR_COST_PER_MILE["default"])

    # Ajuste por odómetro: +5% cada 25,000 millas después de 75,000
    if odometer > 75_000:
        extra_bands = (odometer - 75_000) // 25_000
        base_rate   = base_rate * (1 + extra_bands * 0.05)

    return _round2(total_miles * base_rate)


def calculate_base_fare(
    service_type:     ServiceType,
    distance_miles:   float,
    duration_minutes: float,
    hours_requested:  float = 1.0,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """
    Calcula la tarifa base sin aplicar surge ni extras.

    Returns:
        Tupla (base_fare, distance_fare, time_fare, subtotal_before_surge)
    """
    rates = BASE_FARES[service_type]

    base_fare     = _round2(rates["base"])
    distance_fare = _round2(distance_miles * rates["per_mile"])

    if service_type == ServiceType.RIDE_HOURLY:
        # El hourly cobra por horas completas
        time_fare = _round2(max(1.0, hours_requested) * rates["base"])
        base_fare = Decimal("0")
    else:
        time_fare = _round2(duration_minutes * rates["per_minute"])

    subtotal = base_fare + distance_fare + time_fare
    # Aplicar tarifa mínima
    minimum  = _round2(rates["minimum"])
    subtotal = max(subtotal, minimum)

    return base_fare, distance_fare, time_fare, subtotal


def calculate_surge(
    base_subtotal:   Decimal,
    multiplier:      float,
    service_type:    ServiceType,
) -> Decimal:
    """
    Aplica surge pricing. No aplica en Airport (precio fijo) ni Hourly.

    El surge se aplica solo a la parte de distancia + tiempo (no a la tarifa base).
    Esto es más justo que aplicarlo al total, ya que el pasajero no paga el doble
    de la tarifa de apertura en momentos de alta demanda.

    Args:
        base_subtotal: Tarifa antes de surge.
        multiplier:    Factor de surge (1.0 = sin surge, 2.0 = doble precio).
        service_type:  Tipo de servicio.

    Returns:
        Subtotal con surge aplicado.
    """
    no_surge_types = {ServiceType.RIDE_AIRPORT, ServiceType.RIDE_HOURLY}
    if service_type in no_surge_types or multiplier <= 1.0:
        return base_subtotal

    return _round2(base_subtotal * Decimal(str(multiplier)))


def calculate_extras_total(extras: list[Extra]) -> Decimal:
    """Suma todos los cargos adicionales seleccionados."""
    return _round2(sum(EXTRA_CHARGES.get(e, 0.0) for e in extras))


def determine_profitability(margin_pct: Decimal) -> TripProfitability:
    """Clasifica la rentabilidad del viaje para el conductor."""
    pct = float(margin_pct)
    if pct >= 0.70:
        return TripProfitability.EXCELLENT
    if pct >= 0.50:
        return TripProfitability.GOOD
    if pct >= 0.30:
        return TripProfitability.MODERATE
    return TripProfitability.POOR


def generate_ai_recommendation(
    profitability:    TripProfitability,
    net_per_mile:     Decimal,
    service_type:     ServiceType,
    surge_multiplier: float,
    distance_miles:   float,
) -> str:
    """
    Genera una recomendación textual basada en los datos del viaje.
    En producción, este texto puede ser generado por la API de Claude
    con contexto histórico del conductor.
    """
    recommendations = {
        TripProfitability.EXCELLENT: (
            f"✦ Excelente viaje — ${net_per_mile:.2f}/mi neto. "
            f"{'Surge activo de ' + str(surge_multiplier) + '× aumenta tu ganancia. ' if surge_multiplier > 1 else ''}"
            f"{'Viajes largos como este maximizan tu eficiencia.' if distance_miles > 8 else 'Buen retorno en distancia corta.'}"
        ),
        TripProfitability.GOOD: (
            f"Buen viaje — ${net_per_mile:.2f}/mi neto. "
            f"Margen sólido. "
            f"{'Considera activar Price Lock antes de aceptar.' if surge_multiplier > 1.5 else ''}"
        ),
        TripProfitability.MODERATE: (
            f"Viaje moderado — ${net_per_mile:.2f}/mi neto. "
            f"{'Food delivery corto con tiempo de espera en restaurante reduce el margen.' if service_type == ServiceType.FOOD_DELIVERY else ''}"
            f"Puede mejorar si el pasajero deja propina."
        ),
        TripProfitability.POOR: (
            f"⚠ Margen bajo — ${net_per_mile:.2f}/mi neto. "
            f"{'Considera rechazar y esperar un viaje más largo.' if distance_miles < 2 else ''}"
            f"El costo de combustible y desgaste reduce la ganancia en esta distancia."
        ),
    }
    return recommendations.get(profitability, "Sin recomendación disponible.")


# ─────────────────────────────────────────────────────────────────────────────
# Función principal de pricing
# ─────────────────────────────────────────────────────────────────────────────

async def calculate_trip_pricing(
    request:     RidePricingRequest,
    eia_api_key: Optional[str] = None,
) -> TripPricingResult:
    """
    Calcula el precio completo de un viaje y el desglose de costos para el conductor.

    Este es el núcleo del modelo de negocio de Velo:
    el conductor ve TODA la información antes de aceptar.

    Flujo:
    1. Obtener precio del gas en tiempo real (EIA API)
    2. Calcular tarifa base (por distancia + tiempo + tipo de servicio)
    3. Aplicar surge pricing si hay alta demanda
    4. Sumar extras seleccionados
    5. Calcular costos reales del conductor (gas + desgaste + tiempo muerto)
    6. Calcular ganancia neta y margen
    7. Generar recomendación

    Args:
        request:     Datos del viaje y del vehículo del conductor.
        eia_api_key: Clave de API de la EIA para precio del gas en tiempo real.

    Returns:
        TripPricingResult con desglose completo para conductor y pasajero.
    """

    # ── 1. Precio del gas ──────────────────────────────────────────────────
    gas_price = await fetch_gas_price(request.city_state, eia_api_key)

    # ── 2. Tarifa base ─────────────────────────────────────────────────────
    base_fare, distance_fare, time_fare, subtotal_before_surge = calculate_base_fare(
        service_type     = request.service_type,
        distance_miles   = request.distance_miles,
        duration_minutes = request.duration_minutes,
        hours_requested  = request.hours_requested,
    )

    # ── 3. Surge pricing ───────────────────────────────────────────────────
    surge_multiplier = Decimal(str(request.demand_multiplier))
    subtotal_after_surge = calculate_surge(
        base_subtotal = subtotal_before_surge,
        multiplier    = request.demand_multiplier,
        service_type  = request.service_type,
    )

    # ── 4. Extras ──────────────────────────────────────────────────────────
    extras_total = calculate_extras_total(request.extras)

    # Round trip: doble la tarifa de viaje (no los extras)
    if Extra.ROUND_TRIP in request.extras:
        subtotal_after_surge = _round2(subtotal_after_surge * Decimal("2"))

    # ── 5. Tarifa final del pasajero ───────────────────────────────────────
    booking_fee  = _round2(1.50)
    total_fare   = subtotal_after_surge + extras_total + booking_fee

    fare = FareBreakdown(
        base_fare        = base_fare,
        distance_fare    = distance_fare,
        time_fare        = time_fare,
        surge_multiplier = surge_multiplier,
        extras_total     = extras_total,
        subtotal         = subtotal_after_surge,
        booking_fee      = booking_fee,
        total_fare       = total_fare,
    )

    # ── 6. Costos del conductor ────────────────────────────────────────────
    # Millas totales = viaje + trayecto en vacío hasta el pickup
    total_miles_driven = request.distance_miles + request.deadhead_miles

    fuel_cost = calculate_fuel_cost(
        total_miles = total_miles_driven,
        mpg         = request.driver_vehicle.mpg,
        gas_price   = gas_price,
    )

    wear_cost = calculate_wear_cost(
        total_miles  = total_miles_driven,
        vehicle_type = request.driver_vehicle.vehicle_type,
        odometer     = request.driver_vehicle.odometer,
    )

    # Costo del trayecto en vacío (proporcional, solo combustible + desgaste)
    deadhead_fuel = calculate_fuel_cost(
        total_miles = request.deadhead_miles,
        mpg         = request.driver_vehicle.mpg,
        gas_price   = gas_price,
    )
    deadhead_wear = calculate_wear_cost(
        total_miles  = request.deadhead_miles,
        vehicle_type = request.driver_vehicle.vehicle_type,
        odometer     = request.driver_vehicle.odometer,
    )
    deadhead_cost = _round2(deadhead_fuel + deadhead_wear)

    # Comisión de la plataforma (sobre la tarifa sin booking_fee)
    commission_pct = Decimal(str(request.platform_commission))
    platform_fee   = _round2(subtotal_after_surge * commission_pct)

    total_costs = _round2(fuel_cost + wear_cost + platform_fee)

    costs = CostBreakdown(
        fuel_cost    = fuel_cost,
        wear_cost    = wear_cost,
        deadhead_cost= deadhead_cost,
        platform_fee = platform_fee,
        total_costs  = total_costs,
    )

    # ── 7. Ganancias del conductor ─────────────────────────────────────────
    # Lo que transfiere la plataforma: tarifa del viaje − comisión
    gross_earnings = _round2(subtotal_after_surge - platform_fee)

    # Ganancia neta real: gross − combustible − desgaste
    driver_net = _round2(gross_earnings - fuel_cost - wear_cost)

    # Métricas de eficiencia
    net_per_mile   = _round2(driver_net / Decimal(str(request.distance_miles))) if request.distance_miles > 0 else Decimal("0")
    net_per_minute = _round2(driver_net / Decimal(str(request.duration_minutes))) if request.duration_minutes > 0 else Decimal("0")

    # Margen neto (driver_net / gross_earnings)
    margin_pct = (
        _round2(driver_net / gross_earnings)
        if gross_earnings > 0
        else Decimal("0")
    )

    # ── 8. Clasificación y recomendación ──────────────────────────────────
    profitability    = determine_profitability(margin_pct)
    ai_recommendation = generate_ai_recommendation(
        profitability    = profitability,
        net_per_mile     = net_per_mile,
        service_type     = request.service_type,
        surge_multiplier = float(surge_multiplier),
        distance_miles   = request.distance_miles,
    )

    price_locked = Extra.PRICE_LOCK in request.extras

    return TripPricingResult(
        fare                     = fare,
        costs                    = costs,
        gross_earnings           = gross_earnings,
        driver_net               = driver_net,
        net_per_mile             = net_per_mile,
        net_per_minute           = net_per_minute,
        margin_pct               = margin_pct,
        gas_price_used           = _round2(gas_price),
        profitability            = profitability,
        ai_recommendation        = ai_recommendation,
        service_type             = request.service_type,
        distance_miles           = _round2(request.distance_miles),
        duration_minutes         = _round2(request.duration_minutes),
        platform_commission_pct  = commission_pct,
        price_locked             = price_locked,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Función de comparación de servicios (para el asistente IA)
# ─────────────────────────────────────────────────────────────────────────────

async def compare_service_types(
    distance_miles:   float,
    duration_minutes: float,
    driver_vehicle:   DriverVehicleInfo,
    eia_api_key:      Optional[str] = None,
) -> list[TripPricingResult]:
    """
    Calcula el pricing para todos los tipos de servicio compatibles con el vehículo.
    Útil para el asistente IA que recomienda al conductor qué tipo de servicio aceptar.

    Returns:
        Lista ordenada de resultados por driver_net descendente.
    """
    compatible_services = [
        ServiceType.RIDE_STANDARD,
        ServiceType.RIDE_ECO,
        ServiceType.FOOD_DELIVERY,
        ServiceType.PACKAGE,
    ]

    # SUV y Van pueden hacer XL y PetRide
    if driver_vehicle.vehicle_type in ("suv", "van"):
        compatible_services += [ServiceType.RIDE_XL, ServiceType.PET_RIDE]

    # Luxury puede hacer Premium
    if driver_vehicle.vehicle_type == "luxury":
        compatible_services.append(ServiceType.RIDE_PREMIUM)

    tasks = [
        calculate_trip_pricing(
            request=RidePricingRequest(
                service_type     = svc,
                distance_miles   = distance_miles,
                duration_minutes = duration_minutes,
                driver_vehicle   = driver_vehicle,
            ),
            eia_api_key=eia_api_key,
        )
        for svc in compatible_services
    ]

    results = await asyncio.gather(*tasks)
    return sorted(results, key=lambda r: r.driver_net, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Surge pricing dinámico (lógica de cálculo del multiplicador)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SurgePricingEngine:
    """
    Calcula el multiplicador de surge en tiempo real basado en la
    relación demanda/oferta en una zona geográfica.

    En producción, este engine consulta Redis para la demanda y oferta
    actuales por zona (hexágono H3 o radio de 0.5 millas).
    """
    max_multiplier: float = 3.5
    surge_tiers: list[tuple[float, float]] = field(default_factory=lambda: [
        # (ratio demanda/oferta, multiplicador)
        (1.0,  1.0),   # oferta suficiente
        (1.5,  1.2),   # ligera escasez
        (2.0,  1.5),   # demanda moderada
        (2.5,  1.8),   # alta demanda
        (3.0,  2.2),   # demanda muy alta
        (4.0,  2.8),   # crítico
        (5.0,  3.5),   # máximo
    ])

    def calculate_multiplier(
        self,
        active_requests:   int,
        available_drivers: int,
        is_airport_service: bool = False,
    ) -> float:
        """
        Calcula el multiplicador de surge.

        Args:
            active_requests:   Solicitudes activas en la zona.
            available_drivers: Conductores disponibles en la zona.
            is_airport_service: Si es True, no aplica surge (precio fijo).

        Returns:
            Multiplicador de surge (1.0 = sin surge).
        """
        if is_airport_service:
            return 1.0

        if available_drivers == 0:
            return self.max_multiplier

        ratio = active_requests / available_drivers

        multiplier = 1.0
        for threshold, factor in self.surge_tiers:
            if ratio >= threshold:
                multiplier = factor
            else:
                break

        return min(multiplier, self.max_multiplier)

    def format_surge_label(self, multiplier: float) -> str:
        """Devuelve una etiqueta legible para el surge actual."""
        if multiplier <= 1.0:
            return "Precio normal"
        if multiplier <= 1.5:
            return f"{multiplier}× — Demanda moderada"
        if multiplier <= 2.5:
            return f"{multiplier}× — Alta demanda"
        return f"{multiplier}× — Demanda muy alta"


# ─────────────────────────────────────────────────────────────────────────────
# Demo / script de prueba (python pricing.py)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import json

    async def demo():
        print("\n" + "═" * 60)
        print("  VELO — Demo de pricing.py")
        print("═" * 60)

        # Caso 1: Ride estándar al aeropuerto JFK (14.2 millas)
        request = RidePricingRequest(
            service_type     = ServiceType.RIDE_STANDARD,
            distance_miles   = 14.2,
            duration_minutes = 38,
            deadhead_miles   = 0.8,
            demand_multiplier= 1.4,   # surge 1.4×
            extras           = [Extra.EXTRA_LUGGAGE, Extra.PRICE_LOCK],
            driver_vehicle   = DriverVehicleInfo(
                vehicle_type = "sedan",
                mpg          = 32,
                year         = 2022,
                make         = "Toyota",
                model        = "Camry",
                odometer     = 48_320,
            ),
            city_state       = "New York, NY",
        )

        # Sin clave de API real, usa el precio fallback
        result = await calculate_trip_pricing(request, eia_api_key=None)

        print(f"\n📍 Ruta: Times Square → JFK Airport")
        print(f"   Tipo: {result.service_type.value} | {result.distance_miles} mi | {result.duration_minutes} min")
        print(f"\n💰 TARIFA AL PASAJERO")
        print(f"   Base:          ${result.fare.base_fare}")
        print(f"   Por distancia: ${result.fare.distance_fare}")
        print(f"   Por tiempo:    ${result.fare.time_fare}")
        print(f"   Surge ({result.fare.surge_multiplier}×): incluido en subtotal")
        print(f"   Extras:        ${result.fare.extras_total}")
        print(f"   Booking fee:   ${result.fare.booking_fee}")
        print(f"   ─────────────────────────────")
        print(f"   TOTAL:         ${result.fare.total_fare}")

        print(f"\n🔧 COSTOS REALES DEL CONDUCTOR")
        print(f"   Combustible:   ${result.costs.fuel_cost}  (gas: ${result.gas_price_used}/gal)")
        print(f"   Desgaste:      ${result.costs.wear_cost}")
        print(f"   Tiempo muerto: ${result.costs.deadhead_cost}")
        print(f"   Comisión ({float(result.platform_commission_pct)*100:.0f}%): ${result.costs.platform_fee}")
        print(f"   ─────────────────────────────")
        print(f"   TOTAL COSTOS:  ${result.costs.total_costs}")

        print(f"\n📊 RESULTADO PARA EL CONDUCTOR")
        print(f"   Bruto (plataforma paga): ${result.gross_earnings}")
        print(f"   Neto real:               ${result.driver_net}")
        print(f"   Por milla:               ${result.net_per_mile}/mi")
        print(f"   Por minuto:              ${result.net_per_minute}/min")
        print(f"   Margen:                  {float(result.margin_pct)*100:.1f}%")
        print(f"   Rentabilidad:            {result.profitability.value.upper()}")
        print(f"\n✦ IA recomienda: {result.ai_recommendation}")

        # Caso 2: Comparativa de servicios
        print("\n" + "═" * 60)
        print("  Comparativa de tipos de servicio — misma distancia")
        print("═" * 60)
        vehicle = DriverVehicleInfo(vehicle_type="suv", mpg=24, odometer=30_000)
        comparison = await compare_service_types(
            distance_miles   = 5.0,
            duration_minutes = 20,
            driver_vehicle   = vehicle,
        )
        print(f"\n{'Servicio':<22} {'Tarifa':>8} {'Neto':>8} {'Margen':>8} {'Rentabilidad'}")
        print("─" * 65)
        for r in comparison:
            print(
                f"{r.service_type.value:<22} "
                f"${r.fare.total_fare:>7} "
                f"${r.driver_net:>7} "
                f"{float(r.margin_pct)*100:>7.1f}% "
                f"{r.profitability.value}"
            )

        # Caso 3: Surge engine demo
        print("\n" + "═" * 60)
        engine = SurgePricingEngine()
        scenarios = [(2, 10), (5, 4), (12, 3), (20, 2), (30, 1)]
        print(f"  Surge Engine — ratio demanda/oferta")
        print("═" * 60)
        for req_count, drv_count in scenarios:
            mult  = engine.calculate_multiplier(req_count, drv_count)
            label = engine.format_surge_label(mult)
            print(f"  {req_count:>3} solicitudes / {drv_count:>2} conductores → {label}")

    asyncio.run(demo())
