"""
models.py — Muevo B2B
======================
Modelos de base de datos PostgreSQL con SQLAlchemy ORM.

Tablas principales:
  users          → usuarios (conductores + admins corporativos)
  drivers        → perfil extendido del conductor
  vehicles       → vehículos registrados por conductor
  certifications → permisos y documentos del conductor
  companies      → clientes corporativos B2B
  contracts      → contratos entre empresa y Muevo
  routes         → rutas programadas multi-parada
  stops          → paradas individuales de cada ruta
  deliveries     → confirmaciones de entrega (PoD)
  earnings       → liquidaciones financieras por ruta
  driver_metrics → métricas de eficiencia del conductor

Correr migraciones:
  alembic init alembic
  alembic revision --autogenerate -m "initial"
  alembic upgrade head
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, Numeric, String, Text, JSON,
    Enum, UniqueConstraint, CheckConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

Base = declarative_base()

# ─── Helpers ──────────────────────────────────────────────────────────────────

def uuid_pk():
    """Primary key UUID generado automáticamente."""
    return Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

def timestamps():
    """Columnas created_at / updated_at automáticas."""
    return (
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default=func.now(),
               onupdate=func.now(), nullable=False),
    )

# ─── Enums ────────────────────────────────────────────────────────────────────

class UserRole(str, PyEnum):
    DRIVER        = "driver"
    CORP_ADMIN    = "corp_admin"      # admin de empresa cliente
    MUEVO_ADMIN   = "muevo_admin"     # admin interno Muevo
    DISPATCHER    = "dispatcher"      # despachador de rutas

class VehicleType(str, PyEnum):
    MOTO          = "moto"
    SEDAN         = "sedan"
    FURGONETA     = "furgoneta"
    FURGON        = "furgon"          # furgón de carga

class ServiceMode(str, PyEnum):
    MENSAJERIA    = "mensajeria"      # documentos / paquetes
    LOGISTICA     = "logistica"       # almacén / carga / palets
    EMPLEADOS     = "empleados"       # transporte corporativo
    MIXTO         = "mixto"

class RouteStatus(str, PyEnum):
    DRAFT         = "draft"           # en preparación por el despachador
    PUBLISHED     = "published"       # visible para conductores
    ASSIGNED      = "assigned"        # conductor asignado, pendiente inicio
    IN_PROGRESS   = "in_progress"     # ruta activa
    COMPLETED     = "completed"       # todas las paradas confirmadas
    CANCELLED     = "cancelled"
    FAILED        = "failed"          # no completada por fuerza mayor

class StopStatus(str, PyEnum):
    PENDING       = "pending"
    IN_TRANSIT    = "in_transit"      # conductor en camino a esta parada
    ARRIVED       = "arrived"         # llegó pero aún no confirma
    COMPLETED     = "completed"       # PoD capturado
    SKIPPED       = "skipped"         # omitida con justificación
    FAILED        = "failed"

class PodType(str, PyEnum):
    SIGNATURE     = "signature"
    PHOTO         = "photo"
    BOTH          = "both"
    QR_SCAN       = "qr_scan"
    PIN_CODE      = "pin_code"

class CertStatus(str, PyEnum):
    PENDING       = "pending"
    ACTIVE        = "active"
    EXPIRED       = "expired"
    REJECTED      = "rejected"

class PaymentStatus(str, PyEnum):
    PENDING       = "pending"
    PROCESSING    = "processing"
    PAID          = "paid"
    FAILED        = "failed"
    REFUNDED      = "refunded"

# ─── USERS ────────────────────────────────────────────────────────────────────

class User(Base):
    """
    Tabla base de usuarios. Sirve tanto a conductores como a
    administradores corporativos y staff de Muevo.
    """
    __tablename__ = "users"

    id                = uuid_pk()
    email             = Column(String(255), unique=True, nullable=False, index=True)
    phone             = Column(String(30), unique=True, nullable=True)
    full_name         = Column(String(255), nullable=False)
    avatar_initials   = Column(String(4),   nullable=True)
    avatar_url        = Column(String(512),  nullable=True)
    role              = Column(Enum(UserRole, values_callable=lambda obj: [e.value for e in obj]), nullable=False, default=UserRole.DRIVER)
    hashed_password   = Column(String(255), nullable=False)
    is_active         = Column(Boolean, default=True, nullable=False)
    is_verified       = Column(Boolean, default=False, nullable=False)
    preferred_language= Column(String(10), default="es", nullable=False)
    timezone          = Column(String(64),  default="America/New_York", nullable=False)
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_login_at     = Column(DateTime(timezone=True), nullable=True)

    # Relaciones
    driver            = relationship("Driver",  back_populates="user",    uselist=False)
    company_admin     = relationship("CompanyAdmin", back_populates="user", uselist=False)

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"

# ─── DRIVERS ──────────────────────────────────────────────────────────────────

class Driver(Base):
    """
    Perfil extendido del conductor. Contiene métricas, estado online
    y la relación con sus vehículos y certificaciones.
    """
    __tablename__ = "drivers"

    id                  = uuid_pk()
    user_id             = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                                 unique=True, nullable=False)

    # Estado operativo
    is_online           = Column(Boolean, default=False, nullable=False)
    is_approved         = Column(Boolean, default=False, nullable=False)  # aprobado por Muevo
    current_lat         = Column(Float, nullable=True)
    current_lng         = Column(Float, nullable=True)
    last_location_at    = Column(DateTime(timezone=True), nullable=True)

    # Capacidades declaradas
    service_modes       = Column(ARRAY(String), nullable=False, default=list)
    # ["mensajeria", "logistica", "empleados"]

    # Métricas generales
    total_routes        = Column(Integer, default=0, nullable=False)
    completed_routes    = Column(Integer, default=0, nullable=False)
    cancelled_routes    = Column(Integer, default=0, nullable=False)
    avg_rating          = Column(Numeric(3, 2), default=5.00, nullable=False)
    total_earnings_net  = Column(Numeric(12, 2), default=0.00, nullable=False)
    total_km_driven     = Column(Numeric(10, 2), default=0.00, nullable=False)

    # Stripe para pagos al conductor
    stripe_account_id   = Column(String(255), nullable=True)
    stripe_onboarded    = Column(Boolean, default=False, nullable=False)

    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    user                = relationship("User",          back_populates="driver")
    vehicles            = relationship("Vehicle",        back_populates="driver", cascade="all, delete-orphan")
    certifications      = relationship("Certification",  back_populates="driver", cascade="all, delete-orphan")
    routes              = relationship("Route",          back_populates="driver")
    earnings            = relationship("Earning",        back_populates="driver")
    metrics             = relationship("DriverMetric",   back_populates="driver", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_drivers_is_online", "is_online"),
        Index("ix_drivers_location",  "current_lat", "current_lng"),
    )


# ─── TRANSPORT COMPANIES — Empresas de transporte (modelo B2B2B) ──────────────

class TransportCompany(Base):
    """
    Empresa de transporte con flota propia. Reemplaza al modelo de
    conductor independiente: Muevo conecta clientes corporativos con
    empresas de transporte verificadas, no con personas individuales.
    """
    __tablename__ = "transport_companies"

    id                  = uuid_pk()
    name                 = Column(String(255), nullable=False)
    legal_name            = Column(String(255), nullable=True)
    tax_id                 = Column(String(50), nullable=True, unique=True)   # EIN

    # Verificación regulatoria (anti-fraude)
    dot_number              = Column(String(30), nullable=True)   # DOT number FMCSA
    mc_number                 = Column(String(30), nullable=True)   # MC number (broker/carrier authority)
    insurance_policy_number    = Column(String(100), nullable=True)
    insurance_expires            = Column(DateTime(timezone=True), nullable=True)
    is_verified                    = Column(Boolean, default=False, nullable=False)
    verified_at                      = Column(DateTime(timezone=True), nullable=True)
    verified_by                        = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Contacto
    contact_name                         = Column(String(255), nullable=True)
    contact_email                          = Column(String(255), nullable=True)
    contact_phone                            = Column(String(30), nullable=True)

    # Dirección base de operación
    address_line1                              = Column(String(255), nullable=True)
    city                                          = Column(String(100), nullable=True)
    state                                          = Column(String(50), nullable=True)
    zip_code                                        = Column(String(20), nullable=True)
    base_lat                                          = Column(Float, nullable=True)
    base_lng                                           = Column(Float, nullable=True)

    # Estado operativo
    is_active                                            = Column(Boolean, default=True, nullable=False)
    service_modes                                          = Column(ARRAY(String), nullable=False, default=list)
    # ["mensajeria", "logistica", "empleados"]

    # Métricas (usadas por el algoritmo de matching del dispatcher)
    total_routes                                             = Column(Integer, default=0, nullable=False)
    completed_routes                                          = Column(Integer, default=0, nullable=False)
    rejected_routes                                            = Column(Integer, default=0, nullable=False)
    avg_rating                                                  = Column(Numeric(3, 2), default=5.00, nullable=False)
    on_time_pct                                                  = Column(Numeric(5, 2), default=100.00, nullable=False)
    total_earnings_net                                             = Column(Numeric(14, 2), default=0.00, nullable=False)

    # Pagos
    stripe_account_id                                                = Column(String(255), nullable=True)
    stripe_onboarded                                                   = Column(Boolean, default=False, nullable=False)

    notes                                                                = Column(Text, nullable=True)
    created_at                                                            = Column(DateTime(timezone=True), server_default=func.now())
    updated_at                                                             = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    vehicles    = relationship("Vehicle", back_populates="transport_company", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_tc_is_active",   "is_active"),
        Index("ix_tc_is_verified", "is_verified"),
        Index("ix_tc_location",     "base_lat", "base_lng"),
    )

    def __repr__(self):
        return f"<TransportCompany {self.name} verified={self.is_verified}>"

# ─── VEHICLES ─────────────────────────────────────────────────────────────────

class Vehicle(Base):
    """
    Vehículos registrados por la empresa de transporte (modelo B2B2B).
    Una empresa de transporte puede tener múltiples vehículos en su flota.
    El tipo de vehículo determina qué contratos corporativos puede ejecutar.
    driver_id se mantiene nullable para compatibilidad con el modelo anterior
    de conductor independiente; transport_company_id es el modelo vigente.
    """
    __tablename__ = "vehicles"

    id                   = uuid_pk()
    driver_id             = Column(UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"),
                                  nullable=True)
    transport_company_id  = Column(UUID(as_uuid=True), ForeignKey("transport_companies.id", ondelete="CASCADE"),
                                  nullable=True)
    is_active              = Column(Boolean, default=True, nullable=False)

    # Identificación
    vehicle_type      = Column(Enum(VehicleType, values_callable=lambda obj: [e.value for e in obj]), nullable=False)
    make              = Column(String(100), nullable=False)   # Ford, Toyota...
    model             = Column(String(100), nullable=False)   # Transit, Camry...
    year              = Column(Integer,     nullable=False)
    color             = Column(String(50),  nullable=False)
    plate             = Column(String(20),  nullable=False, unique=True)
    vin               = Column(String(17),  nullable=True)

    # Capacidad de carga (relevante para logística)
    payload_kg        = Column(Numeric(8, 2), nullable=True)   # kg de carga máxima
    volume_m3         = Column(Numeric(6, 2), nullable=True)   # m³ útiles
    passenger_seats   = Column(Integer, default=1, nullable=False)

    # Eficiencia
    mpg               = Column(Numeric(6, 2), nullable=True)
    fuel_type         = Column(String(20), default="gasoline", nullable=False)
    # gasoline | diesel | electric | hybrid

    # Odómetro
    odometer_miles    = Column(Integer, default=0, nullable=False)
    odometer_updated_at = Column(DateTime(timezone=True), nullable=True)

    # Documentos
    insurance_url     = Column(String(512), nullable=True)
    insurance_expires = Column(DateTime(timezone=True), nullable=True)
    inspection_url    = Column(String(512), nullable=True)
    inspection_expires= Column(DateTime(timezone=True), nullable=True)
    registration_url  = Column(String(512), nullable=True)

    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    driver             = relationship("Driver",           back_populates="vehicles")
    transport_company   = relationship("TransportCompany", back_populates="vehicles")
    routes               = relationship("Route",            back_populates="vehicle")

    __table_args__ = (
        Index("ix_vehicles_driver_id", "driver_id"),
        Index("ix_vehicles_plate",     "plate"),
        CheckConstraint("year >= 2000 AND year <= 2030", name="ck_vehicle_year"),
    )

# ─── CERTIFICATIONS ───────────────────────────────────────────────────────────

class Certification(Base):
    """
    Permisos, licencias y certificaciones del conductor.
    Algunos contratos B2B requieren certificaciones específicas.
    """
    __tablename__ = "certifications"

    id                = uuid_pk()
    driver_id         = Column(UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"),
                               nullable=False)

    cert_type         = Column(String(100), nullable=False)
    # cargo_license | merch_insurance | port_access | hazmat | inspection | cdl

    label             = Column(String(255), nullable=False)
    # "Licencia de transporte de carga", "Seguro de mercancía"...

    status            = Column(Enum(CertStatus, values_callable=lambda obj: [e.value for e in obj]), default=CertStatus.PENDING, nullable=False)
    document_url      = Column(String(512), nullable=True)   # S3 / Cloudflare R2
    issued_at         = Column(DateTime(timezone=True), nullable=True)
    expires_at        = Column(DateTime(timezone=True), nullable=True)
    issuing_authority = Column(String(255), nullable=True)
    notes             = Column(Text, nullable=True)

    # Revisión por staff Muevo
    reviewed_by       = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at       = Column(DateTime(timezone=True), nullable=True)
    rejection_reason  = Column(Text, nullable=True)

    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    driver            = relationship("Driver", back_populates="certifications")

    __table_args__ = (
        Index("ix_certs_driver_id", "driver_id"),
        Index("ix_certs_expires",   "expires_at"),
    )

# ─── HOLDINGS ─────────────────────────────────────────────────────────────────

class HoldingType(str, PyEnum):
    SINGLE           = "single"            # holding con una sola compañía (default, 90% de clientes)
    MULTI_SUBSIDIARY = "multi_subsidiary"   # holding real con varias subsidiarias (EIN propios)
    FRANCHISE_GROUP  = "franchise_group"    # agrupación administrativa de franquicias independientes

class BillingMode(str, PyEnum):
    PER_COMPANY           = "per_company"            # cada compañía recibe su propia factura
    CONSOLIDATED           = "consolidated"            # una factura para el holding, desglosada por compañía
    CONSOLIDATED_SUMMARY   = "consolidated_summary"    # una factura, solo el total

class Holding(Base):
    """
    Capa superior a Company. TODA compañía pertenece siempre a un holding,
    incluso si ese holding contiene una sola compañía (holding_type='single_entity').
    Esto elimina lógica condicional: el sistema nunca pregunta "¿tiene holding?".
    Los campos de esta clase reflejan EXACTAMENTE las columnas creadas por la
    migración 002_holdings_warehouses.py — no agregar campos sin migrar primero.
    """
    __tablename__ = "holdings"

    id                       = uuid_pk()
    name                     = Column(String(255), nullable=False)
    legal_name               = Column(String(255), nullable=True)
    tax_id                   = Column(String(50), nullable=True, unique=True)  # EIN del holding, si aplica

    holding_type             = Column(String(30), nullable=False, default="single_entity")
    # single_entity | parent_subsidiary | franchise_group

    billing_mode             = Column(String(20), nullable=False, default="per_company")
    # per_company | consolidated | consolidated_simple

    billing_email            = Column(String(255), nullable=True)
    payment_terms_days       = Column(Integer, default=30, nullable=False)
    stripe_customer_id       = Column(String(255), nullable=True)
    credit_limit             = Column(Numeric(14, 2), default=0.00, nullable=False)

    # Contacto del holding (admin master — ve todas las subsidiarias)
    primary_contact_name     = Column(String(255), nullable=True)
    primary_contact_email    = Column(String(255), nullable=True)
    primary_contact_phone    = Column(String(30),  nullable=True)

    # Configuración heredada por defecto a todas las companies del holding
    default_allowed_modes    = Column(ARRAY(String), nullable=False, default=list)
    default_required_certs   = Column(ARRAY(String), nullable=False, default=list)

    industry                 = Column(String(100), nullable=True)
    country                  = Column(String(50), default="US", nullable=False)
    is_active                = Column(Boolean, default=True, nullable=False)
    logo_url                 = Column(String(512), nullable=True)
    notes                    = Column(Text, nullable=True)

    created_at               = Column(DateTime(timezone=True), server_default=func.now())
    updated_at                = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    companies                 = relationship("Company",     back_populates="holding")
    holding_users              = relationship("HoldingUser", back_populates="holding", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Holding {self.name} ({self.holding_type})>"


class HoldingRole(str, PyEnum):
    MASTER_ADMIN  = "master_admin"   # ve y gestiona todas las compañías del holding
    BILLING_ADMIN = "billing_admin"  # solo facturación consolidada
    VIEWER        = "viewer"         # solo lectura de reportes agregados

class HoldingUser(Base):
    """Usuario con visibilidad/gestión a nivel de TODO el holding (todas sus subsidiarias)."""
    __tablename__ = "holding_users"

    id          = uuid_pk()
    holding_id  = Column(UUID(as_uuid=True), ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False)
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.id",    ondelete="CASCADE"), nullable=False)

    role        = Column(String(30), nullable=False, default="executive_viewer")
    # holding_admin | executive_viewer | billing_manager

    can_create_companies          = Column(Boolean, default=False, nullable=False)
    can_view_consolidated_billing = Column(Boolean, default=True,  nullable=False)

    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    holding     = relationship("Holding", back_populates="holding_users")
    user        = relationship("User")

    __table_args__ = (
        UniqueConstraint("holding_id", "user_id", name="uq_holding_user"),
    )

# ─── COMPANIES ────────────────────────────────────────────────────────────────

class Company(Base):
    """
    Clientes corporativos B2B — empresas que contratan rutas con Muevo.
    Siempre pertenece a un Holding (holding_id NOT NULL) — ver Holding arriba.
    """
    __tablename__ = "companies"

    id                = uuid_pk()
    holding_id        = Column(UUID(as_uuid=True), ForeignKey("holdings.id"), nullable=True)
    is_primary_company= Column(Boolean, default=True, nullable=False)
    # TRUE  -> única/principal company del holding (caso single_entity)
    # FALSE -> subsidiaria adicional dentro de un parent_subsidiary

    name              = Column(String(255), nullable=False)
    legal_name        = Column(String(255), nullable=True)
    tax_id            = Column(String(50),  nullable=True, unique=True)   # EIN en EEUU
    industry          = Column(String(100), nullable=True)
    # legal | logistics | tech | healthcare | retail...

    # Contacto principal
    contact_name      = Column(String(255), nullable=True)
    contact_email     = Column(String(255), nullable=True)
    contact_phone     = Column(String(30),  nullable=True)

    # Dirección
    address_line1     = Column(String(255), nullable=True)
    address_line2     = Column(String(255), nullable=True)
    city              = Column(String(100), nullable=True)
    state             = Column(String(50),  nullable=True)
    zip_code          = Column(String(20),  nullable=True)
    country           = Column(String(50),  default="US", nullable=False)

    # Configuración de servicios
    allowed_modes     = Column(ARRAY(String), nullable=False, default=list)
    # ["mensajeria", "logistica", "empleados"]
    required_certs    = Column(ARRAY(String), nullable=False, default=list)
    # Certificaciones que deben tener los conductores asignados

    # Facturación
    billing_mode      = Column(String(20), default="inherit", nullable=False)
    # inherit | independent | consolidated
    billing_email     = Column(String(255), nullable=True)
    payment_terms_days= Column(Integer, default=30, nullable=False)  # Net 30
    stripe_customer_id= Column(String(255), nullable=True)
    credit_limit      = Column(Numeric(12, 2), default=0.00, nullable=False)

    is_active         = Column(Boolean, default=True, nullable=False)
    logo_url          = Column(String(512), nullable=True)
    notes             = Column(Text, nullable=True)

    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    holding           = relationship("Holding",      back_populates="companies")
    admins            = relationship("CompanyAdmin", back_populates="company", cascade="all, delete-orphan")
    routes            = relationship("Route",        back_populates="company")
    contracts         = relationship("Contract",     back_populates="company")
    warehouses        = relationship("Warehouse",    back_populates="company", cascade="all, delete-orphan")

# ─── COMPANY ADMINS ───────────────────────────────────────────────────────────

class CompanyAdmin(Base):
    """Usuarios administradores de una empresa cliente."""
    __tablename__ = "company_admins"

    id          = uuid_pk()
    company_id  = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.id",     ondelete="CASCADE"), nullable=False)
    is_primary  = Column(Boolean, default=False, nullable=False)
    can_dispatch= Column(Boolean, default=True,  nullable=False)  # puede crear rutas
    can_invoice = Column(Boolean, default=False, nullable=False)  # puede ver facturas

    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    company     = relationship("Company", back_populates="admins")
    user        = relationship("User",    back_populates="company_admin")

    __table_args__ = (
        UniqueConstraint("company_id", "user_id", name="uq_company_admin"),
    )

# ─── WAREHOUSES ───────────────────────────────────────────────────────────────

class WarehouseType(str, PyEnum):
    DISTRIBUTION  = "distribution"
    RETAIL_STORE  = "retail_store"
    MEDICAL       = "medical"
    LEGAL_OFFICE  = "legal_office"
    MANUFACTURING = "manufacturing"
    EVENT_VENUE   = "event_venue"

class Warehouse(Base):
    """
    Almacén / sucursal de una compañía. Una Company puede tener N warehouses
    (modelo multi-almacén). El origin_warehouse_id de una ruta apunta aquí.
    """
    __tablename__ = "warehouses"

    id                    = uuid_pk()
    company_id            = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    code                  = Column(String(30),  nullable=False)   # código interno (ej: ORL-01)
    name                  = Column(String(255), nullable=False)
    warehouse_type        = Column(String(50), nullable=False, default="distribution")
    # distribution | retail_store | medical | legal_office | manufacturing | event_venue

    # Dirección
    address_line1         = Column(String(255), nullable=False)
    address_line2         = Column(String(255), nullable=True)
    city                  = Column(String(100), nullable=False)
    state                 = Column(String(50),  nullable=False)
    zip_code              = Column(String(20),  nullable=True)
    country               = Column(String(50),  default="US", nullable=False)
    lat                   = Column(Float, nullable=True)
    lng                   = Column(Float, nullable=True)

    # Contacto operativo
    contact_name          = Column(String(255), nullable=True)
    contact_phone         = Column(String(30),  nullable=True)
    contact_email         = Column(String(255), nullable=True)

    # Operación
    docks_count           = Column(Integer, default=1, nullable=False)
    operating_hours       = Column(JSONB, nullable=True)   # {"mon":"07:00-18:00", ...}
    default_wait_minutes  = Column(Integer, default=15, nullable=False)
    timezone              = Column(String(64), default="America/New_York", nullable=False)

    is_origin_default     = Column(Boolean, default=False, nullable=False)
    requires_appointment   = Column(Boolean, default=False, nullable=False)

    is_active             = Column(Boolean, default=True, nullable=False)
    notes                 = Column(Text, nullable=True)

    created_at            = Column(DateTime(timezone=True), server_default=func.now())
    updated_at            = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    company               = relationship("Company", back_populates="warehouses")
    warehouse_users       = relationship("WarehouseUser", back_populates="warehouse", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_warehouse_code"),
        Index("ix_warehouses_company_id", "company_id"),
        Index("ix_warehouses_city_state", "city", "state"),
        Index("ix_warehouses_location", "lat", "lng"),
    )

    def __repr__(self):
        return f"<Warehouse {self.code} — {self.name}>"


class WarehouseRole(str, PyEnum):
    OPERATOR   = "operator"
    SUPERVISOR = "supervisor"
    VIEWER     = "viewer"

class WarehouseUser(Base):
    """Usuario con acceso a un almacén específico (no a toda la compañía)."""
    __tablename__ = "warehouse_users"

    id                  = uuid_pk()
    warehouse_id        = Column(UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False)
    user_id             = Column(UUID(as_uuid=True), ForeignKey("users.id",      ondelete="CASCADE"), nullable=False)
    role                = Column(String(30), nullable=False, default="operator")
    # operator | supervisor | viewer
    can_create_routes   = Column(Boolean, default=True,  nullable=False)
    can_approve_pod     = Column(Boolean, default=False, nullable=False)

    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    warehouse           = relationship("Warehouse", back_populates="warehouse_users")
    user                = relationship("User")

    __table_args__ = (
        UniqueConstraint("warehouse_id", "user_id", name="uq_warehouse_user"),
    )

# ─── CONTRACTS ────────────────────────────────────────────────────────────────

class Contract(Base):
    """
    Contratos marco entre una empresa y Muevo.
    Define tarifas, SLAs y condiciones de servicio.
    """
    __tablename__ = "contracts"

    id                  = uuid_pk()
    company_id          = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)

    contract_number     = Column(String(50), unique=True, nullable=False)
    service_mode        = Column(Enum(ServiceMode, values_callable=lambda obj: [e.value for e in obj]), nullable=False)
    vehicle_type        = Column(Enum(VehicleType, values_callable=lambda obj: [e.value for e in obj]), nullable=True)  # None = cualquier tipo

    # Tarifas acordadas
    base_rate           = Column(Numeric(10, 2), nullable=False)    # tarifa base por ruta
    rate_per_km         = Column(Numeric(8, 4),  nullable=True)     # costo adicional por km
    rate_per_stop       = Column(Numeric(8, 2),  nullable=True)     # costo por parada
    muevo_commission_pct= Column(Numeric(5, 2),  default=12.00, nullable=False)

    # SLA (Service Level Agreement)
    max_delay_minutes   = Column(Integer, default=15, nullable=False)
    penalty_per_delay   = Column(Numeric(8, 2), default=0.00, nullable=False)
    bonus_on_time       = Column(Numeric(8, 2), default=0.00, nullable=False)

    # Vigencia
    start_date          = Column(DateTime(timezone=True), nullable=False)
    end_date            = Column(DateTime(timezone=True), nullable=True)  # None = indefinido
    is_active           = Column(Boolean, default=True, nullable=False)

    signed_document_url = Column(String(512), nullable=True)
    notes               = Column(Text, nullable=True)

    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    company             = relationship("Company",  back_populates="contracts")
    routes              = relationship("Route",    back_populates="contract")

# ─── ROUTES ───────────────────────────────────────────────────────────────────

class Route(Base):
    """
    Ruta programada multi-parada. Es la unidad central del módulo Drive B2B.
    Una ruta tiene N paradas ordenadas óptimamente por IA.
    """
    __tablename__ = "routes"

    id                  = uuid_pk()
    company_id          = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    contract_id         = Column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=True)
    driver_id           = Column(UUID(as_uuid=True), ForeignKey("drivers.id"),   nullable=True)
    vehicle_id          = Column(UUID(as_uuid=True), ForeignKey("vehicles.id"),  nullable=True)
    dispatched_by       = Column(UUID(as_uuid=True), ForeignKey("users.id"),     nullable=True)

    # Identificación
    route_number        = Column(String(50), unique=True, nullable=False)
    title               = Column(String(255), nullable=False)
    service_mode        = Column(Enum(ServiceMode, values_callable=lambda obj: [e.value for e in obj]), nullable=False)
    status              = Column(Enum(RouteStatus, values_callable=lambda obj: [e.value for e in obj]), default=RouteStatus.DRAFT, nullable=False)

    # Programación
    scheduled_date      = Column(DateTime(timezone=True), nullable=False)
    scheduled_start     = Column(DateTime(timezone=True), nullable=False)
    scheduled_end       = Column(DateTime(timezone=True), nullable=False)
    actual_start        = Column(DateTime(timezone=True), nullable=True)
    actual_end          = Column(DateTime(timezone=True), nullable=True)

    # Logística
    total_stops         = Column(Integer, default=0, nullable=False)
    completed_stops     = Column(Integer, default=0, nullable=False)
    total_km_estimated  = Column(Numeric(8, 2), nullable=True)
    total_km_actual     = Column(Numeric(8, 2), nullable=True)
    origin_address      = Column(String(255), nullable=True)  # punto de inicio del conductor

    # Vehículo requerido
    required_vehicle_type = Column(Enum(VehicleType, values_callable=lambda obj: [e.value for e in obj]), nullable=True)
    required_certs        = Column(ARRAY(String), nullable=False, default=list)

    # Financiero
    gross_pay           = Column(Numeric(10, 2), nullable=False)
    muevo_commission    = Column(Numeric(10, 2), nullable=False)
    fuel_cost_estimated = Column(Numeric(8, 2), nullable=True)
    wear_cost_estimated = Column(Numeric(8, 2), nullable=True)
    net_pay_estimated   = Column(Numeric(10, 2), nullable=True)
    net_pay_actual      = Column(Numeric(10, 2), nullable=True)  # post bonos/penalizaciones
    margin_pct          = Column(Numeric(5, 2), nullable=True)

    # SLA
    sla_penalty         = Column(Numeric(8, 2), default=0.00, nullable=False)
    sla_bonus           = Column(Numeric(8, 2), default=0.00, nullable=False)
    on_time_pct         = Column(Numeric(5, 2), nullable=True)

    # Instrucciones
    driver_notes        = Column(Text, nullable=True)
    internal_notes      = Column(Text, nullable=True)

    # Optimización de ruta (resultado del algoritmo IA)
    optimized_order     = Column(JSONB, nullable=True)
    # {"algorithm": "nearest_neighbor", "total_km_saved": 4.2, "computed_at": "..."}

    # Modo logística — instrucciones de carga
    loading_instructions = Column(JSONB, nullable=True)
    # [{"dock": "Muelle 4", "pallets": 3, "wait_minutes": 20}]

    # Modo empleados — lista de pasajeros
    passenger_manifest  = Column(JSONB, nullable=True)
    # [{"name": "...", "stop_id": "...", "checked_in": false}]

    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    company             = relationship("Company",  back_populates="routes")
    contract            = relationship("Contract", back_populates="routes")
    driver              = relationship("Driver",   back_populates="routes")
    vehicle             = relationship("Vehicle",  back_populates="routes")
    stops               = relationship("Stop",     back_populates="route",
                                      cascade="all, delete-orphan",
                                      order_by="Stop.sequence_order")
    earnings            = relationship("Earning",  back_populates="route", uselist=False)

    __table_args__ = (
        Index("ix_routes_company_id",       "company_id"),
        Index("ix_routes_driver_id",        "driver_id"),
        Index("ix_routes_status",           "status"),
        Index("ix_routes_scheduled_date",   "scheduled_date"),
        Index("ix_routes_route_number",     "route_number"),
    )

    def __repr__(self):
        return f"<Route {self.route_number} [{self.status}]>"

# ─── STOPS ────────────────────────────────────────────────────────────────────

class Stop(Base):
    """
    Parada individual dentro de una ruta.
    El campo sequence_order es asignado por el algoritmo de optimización.
    """
    __tablename__ = "stops"

    id                  = uuid_pk()
    route_id            = Column(UUID(as_uuid=True), ForeignKey("routes.id", ondelete="CASCADE"),
                                 nullable=False)

    # Orden físico en la ruta (optimizado por IA)
    sequence_order      = Column(Integer, nullable=False)
    original_order      = Column(Integer, nullable=True)  # orden original del cliente

    status              = Column(Enum(StopStatus, values_callable=lambda obj: [e.value for e in obj]), default=StopStatus.PENDING, nullable=False)

    # Destino
    address_line1       = Column(String(255), nullable=False)
    address_line2       = Column(String(255), nullable=True)
    city                = Column(String(100), nullable=False)
    state               = Column(String(50),  nullable=False)
    zip_code            = Column(String(20),  nullable=True)
    lat                 = Column(Float, nullable=True)
    lng                 = Column(Float, nullable=True)

    # Empresa / receptor
    company_name        = Column(String(255), nullable=True)
    contact_name        = Column(String(255), nullable=True)
    contact_phone       = Column(String(30),  nullable=True)
    contact_email       = Column(String(255), nullable=True)
    floor_suite         = Column(String(100), nullable=True)
    access_notes        = Column(Text, nullable=True)

    # Paquete / entrega
    package_code        = Column(String(100), nullable=True, index=True)
    package_description = Column(String(255), nullable=True)
    package_weight_kg   = Column(Numeric(8, 2), nullable=True)
    is_fragile          = Column(Boolean, default=False, nullable=False)
    is_urgent           = Column(Boolean, default=False, nullable=False)

    # Tiempos
    eta_scheduled       = Column(DateTime(timezone=True), nullable=True)
    eta_current         = Column(DateTime(timezone=True), nullable=True)   # actualizado en tiempo real
    arrived_at          = Column(DateTime(timezone=True), nullable=True)
    completed_at        = Column(DateTime(timezone=True), nullable=True)
    wait_minutes_estimated = Column(Integer, default=0, nullable=False)
    wait_minutes_actual    = Column(Integer, nullable=True)

    # Distancia desde parada anterior
    distance_from_prev_km = Column(Numeric(8, 2), nullable=True)

    # Proof of Delivery requerido
    pod_required        = Column(Enum(PodType, values_callable=lambda obj: [e.value for e in obj]), default=PodType.SIGNATURE, nullable=False)
    skip_reason         = Column(Text, nullable=True)

    # Modo empleados — pasajeros en esta parada
    passengers_count    = Column(Integer, default=0, nullable=False)
    passengers_checked_in = Column(Integer, default=0, nullable=False)

    # Modo logística — instrucciones de carga/descarga
    loading_notes       = Column(Text, nullable=True)
    dock_number         = Column(String(50), nullable=True)

    driver_notes        = Column(Text, nullable=True)

    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    route               = relationship("Route",    back_populates="stops")
    delivery            = relationship("Delivery", back_populates="stop", uselist=False,
                                      cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_stops_route_id",      "route_id"),
        Index("ix_stops_status",        "status"),
        Index("ix_stops_package_code",  "package_code"),
        UniqueConstraint("route_id", "sequence_order", name="uq_stop_sequence"),
    )

    def __repr__(self):
        return f"<Stop {self.sequence_order} — {self.company_name} [{self.status}]>"

# ─── ROUTE HEADERS — Encabezado de ruta (multi-holding/company/warehouse) ─────

class RouteHeader(Base):
    """
    Encabezado de la ruta. Reemplaza/complementa a Route para soportar
    el modelo multi-holding -> multi-company -> multi-warehouse con
    detalle de envío (paquetes, peso, volumen, HazMat, etc.)
    """
    __tablename__ = "route_headers"

    id                      = uuid_pk()
    company_id              = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    origin_warehouse_id     = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=True)
    contract_id             = Column(UUID(as_uuid=True), ForeignKey("contracts.id"),  nullable=True)

    # Asignación (dispatcher)
    transport_company_id    = Column(UUID(as_uuid=True), ForeignKey("transport_companies.id"), nullable=True)
    vehicle_id               = Column(UUID(as_uuid=True), ForeignKey("vehicles.id"), nullable=True)
    dispatched_by            = Column(UUID(as_uuid=True), ForeignKey("users.id"),    nullable=True)
    dispatch_mode            = Column(String(20), default="manual", nullable=False)
    # manual | marketplace_auto | direct_negotiation

    # Identificación
    route_number              = Column(String(50), unique=True, nullable=False)
    external_reference        = Column(String(100), nullable=True)   # ID del ERP/WMS del cliente
    source                    = Column(String(20), default="manual", nullable=False)
    # manual | csv_upload | api | webhook

    title                     = Column(String(255), nullable=False)
    service_mode               = Column(Enum(ServiceMode, values_callable=lambda obj: [e.value for e in obj]), nullable=False)
    status                     = Column(Enum(RouteStatus, values_callable=lambda obj: [e.value for e in obj]), default=RouteStatus.DRAFT, nullable=False)

    # Programación
    scheduled_date              = Column(DateTime(timezone=True), nullable=False)
    scheduled_start             = Column(DateTime(timezone=True), nullable=False)
    scheduled_end               = Column(DateTime(timezone=True), nullable=False)
    actual_start                 = Column(DateTime(timezone=True), nullable=True)
    actual_end                   = Column(DateTime(timezone=True), nullable=True)

    # Vehículo / certificaciones requeridas
    required_vehicle_type        = Column(Enum(VehicleType, values_callable=lambda obj: [e.value for e in obj]), nullable=True)
    required_certs                = Column(ARRAY(String), nullable=False, default=list)

    # Totales agregados (mantenidos por triggers a partir de route_details)
    total_stops                   = Column(Integer, default=0, nullable=False)
    completed_stops                = Column(Integer, default=0, nullable=False)
    total_km_estimated              = Column(Numeric(8, 2), nullable=True)
    total_km_actual                  = Column(Numeric(8, 2), nullable=True)

    # Totales de envío (agregados desde shipment_items vía route_details)
    total_packages                    = Column(Integer, default=0, nullable=False)
    total_weight_lbs                   = Column(Numeric(10, 2), default=0.00, nullable=False)
    total_volume_ft3                    = Column(Numeric(10, 2), default=0.00, nullable=False)
    total_cargo_value                    = Column(Numeric(12, 2), default=0.00, nullable=False)

    # Banderas de carga (true si CUALQUIER parada las requiere)
    has_fragile_items                     = Column(Boolean, default=False, nullable=False)
    has_hazmat_items                       = Column(Boolean, default=False, nullable=False)
    requires_temp_control                   = Column(Boolean, default=False, nullable=False)
    requires_insurance                       = Column(Boolean, default=False, nullable=False)
    stackable                                 = Column(Boolean, default=True, nullable=False)

    # Financiero
    gross_pay                                  = Column(Numeric(10, 2), nullable=False)
    muevo_commission_pct                        = Column(Numeric(5, 2), default=12.00, nullable=False)
    muevo_commission_amt                         = Column(Numeric(10, 2), nullable=False)
    fuel_cost_estimated                           = Column(Numeric(8, 2), nullable=True)
    wear_cost_estimated                            = Column(Numeric(8, 2), nullable=True)
    net_pay_estimated                               = Column(Numeric(10, 2), nullable=True)
    net_pay_actual                                   = Column(Numeric(10, 2), nullable=True)
    margin_pct                                        = Column(Numeric(5, 2), nullable=True)

    # SLA
    sla_max_delay_minutes                              = Column(Integer, default=15, nullable=False)
    sla_penalty                                         = Column(Numeric(8, 2), default=0.00, nullable=False)
    sla_bonus                                            = Column(Numeric(8, 2), default=0.00, nullable=False)
    on_time_pct                                           = Column(Numeric(5, 2), nullable=True)

    # Optimización IA
    optimized_order                                        = Column(JSONB, nullable=True)

    # Notas
    driver_notes                                            = Column(Text, nullable=True)
    internal_notes                                           = Column(Text, nullable=True)

    # Datos específicos por modo
    loading_instructions                                      = Column(JSONB, nullable=True)
    passenger_manifest                                         = Column(JSONB, nullable=True)

    created_at                                                  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at                                                   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    company             = relationship("Company",   foreign_keys=[company_id])
    origin_warehouse     = relationship("Warehouse", foreign_keys=[origin_warehouse_id])
    details               = relationship("RouteDetail", back_populates="route_header",
                                        cascade="all, delete-orphan", order_by="RouteDetail.sequence_order")

    __table_args__ = (
        Index("ix_rh_company_id",      "company_id"),
        Index("ix_rh_warehouse_id",     "origin_warehouse_id"),
        Index("ix_rh_status",           "status"),
        Index("ix_rh_scheduled_date",   "scheduled_date"),
        Index("ix_rh_route_number",     "route_number"),
        Index("ix_rh_external_ref",     "external_reference"),
    )

    def __repr__(self):
        return f"<RouteHeader {self.route_number} [{self.status}]>"


# ─── ROUTE DETAILS — Detalle de ruta (1 fila por parada) ──────────────────────

class RouteDetail(Base):
    """
    Detalle de la ruta — una fila por parada. El destino puede ser otro
    warehouse de Muevo (cross-docking entre companies) o una dirección externa.
    """
    __tablename__ = "route_details"

    id                          = uuid_pk()
    route_header_id              = Column(UUID(as_uuid=True), ForeignKey("route_headers.id", ondelete="CASCADE"), nullable=False)
    destination_warehouse_id      = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=True)

    # Orden físico (resultado del algoritmo de optimización)
    sequence_order                  = Column(Integer, nullable=False)
    original_order                   = Column(Integer, nullable=True)

    status                            = Column(Enum(StopStatus, values_callable=lambda obj: [e.value for e in obj]), default=StopStatus.PENDING, nullable=False)

    # Dirección de destino
    address_line1                      = Column(String(255), nullable=False)
    address_line2                       = Column(String(255), nullable=True)
    city                                  = Column(String(100), nullable=False)
    state                                 = Column(String(50), nullable=False)
    zip_code                              = Column(String(20), nullable=True)
    lat                                     = Column(Float, nullable=True)
    lng                                      = Column(Float, nullable=True)

    # Receptor / contacto
    company_name                              = Column(String(255), nullable=True)
    contact_name                               = Column(String(255), nullable=True)
    contact_phone                               = Column(String(30), nullable=True)
    contact_email                                = Column(String(255), nullable=True)
    floor_suite                                   = Column(String(100), nullable=True)
    access_notes                                   = Column(Text, nullable=True)

    # Totales de envío de ESTA parada (mantenidos por trigger desde shipment_items)
    packages_count                                  = Column(Integer, default=0, nullable=False)
    weight_lbs                                        = Column(Numeric(10, 2), default=0.00, nullable=False)
    volume_ft3                                         = Column(Numeric(10, 2), default=0.00, nullable=False)
    cargo_value                                         = Column(Numeric(12, 2), default=0.00, nullable=False)

    is_fragile                                           = Column(Boolean, default=False, nullable=False)
    is_hazmat                                             = Column(Boolean, default=False, nullable=False)
    is_temp_controlled                                     = Column(Boolean, default=False, nullable=False)
    is_stackable                                            = Column(Boolean, default=True, nullable=False)
    requires_insurance                                       = Column(Boolean, default=False, nullable=False)

    # Operación de almacén (modo logística)
    dock_number                                               = Column(String(50), nullable=True)
    loading_notes                                              = Column(Text, nullable=True)
    wait_minutes_estimated                                      = Column(Integer, default=0, nullable=False)
    wait_minutes_actual                                          = Column(Integer, nullable=True)

    # Tiempos
    eta_scheduled                                                 = Column(DateTime(timezone=True), nullable=True)
    eta_current                                                    = Column(DateTime(timezone=True), nullable=True)
    arrived_at                                                      = Column(DateTime(timezone=True), nullable=True)
    completed_at                                                     = Column(DateTime(timezone=True), nullable=True)
    distance_from_prev_km                                             = Column(Numeric(8, 2), nullable=True)

    # Proof of Delivery
    pod_required                                                       = Column(Enum(PodType, values_callable=lambda obj: [e.value for e in obj]), default=PodType.SIGNATURE, nullable=False)
    skip_reason                                                         = Column(Text, nullable=True)

    # Modo empleados
    passengers_count                                                     = Column(Integer, default=0, nullable=False)
    passengers_checked_in                                                 = Column(Integer, default=0, nullable=False)

    driver_notes                                                           = Column(Text, nullable=True)

    created_at                                                              = Column(DateTime(timezone=True), server_default=func.now())
    updated_at                                                               = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    route_header             = relationship("RouteHeader", back_populates="details")
    destination_warehouse     = relationship("Warehouse",   foreign_keys=[destination_warehouse_id])
    items                       = relationship("ShipmentItem", back_populates="route_detail",
                                              cascade="all, delete-orphan", order_by="ShipmentItem.line_number")
    delivery                     = relationship("Delivery", back_populates="route_detail", uselist=False,
                                              cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_rd_header_id",      "route_header_id"),
        Index("ix_rd_dest_warehouse", "destination_warehouse_id"),
        Index("ix_rd_status",         "status"),
        UniqueConstraint("route_header_id", "sequence_order", name="uq_route_detail_sequence"),
    )

    def __repr__(self):
        return f"<RouteDetail {self.sequence_order} — {self.company_name} [{self.status}]>"


# ─── SHIPMENT ITEMS — Líneas individuales de paquete/SKU por parada ──────────

class ShipmentItem(Base):
    """
    Nivel más granular del envío: cada caja, pallet o SKU dentro de una parada.
    """
    __tablename__ = "shipment_items"

    id                  = uuid_pk()
    route_detail_id      = Column(UUID(as_uuid=True), ForeignKey("route_details.id", ondelete="CASCADE"), nullable=False)

    line_number            = Column(Integer, nullable=False)
    package_code             = Column(String(100), nullable=True, index=True)
    description                = Column(String(255), nullable=True)

    item_type                    = Column(String(30), default="box", nullable=False)
    # box | pallet | envelope | crate | container | bag

    quantity                       = Column(Integer, default=1, nullable=False)
    weight_lbs                       = Column(Numeric(8, 2), default=0.00, nullable=False)
    length_in                         = Column(Numeric(6, 2), nullable=True)
    width_in                           = Column(Numeric(6, 2), nullable=True)
    height_in                           = Column(Numeric(6, 2), nullable=True)
    volume_ft3                           = Column(Numeric(8, 2), nullable=True)

    declared_value                         = Column(Numeric(10, 2), default=0.00, nullable=False)

    is_fragile                               = Column(Boolean, default=False, nullable=False)
    is_hazmat                                 = Column(Boolean, default=False, nullable=False)
    hazmat_class                               = Column(String(20), nullable=True)
    requires_signature                          = Column(Boolean, default=True, nullable=False)

    picked_up                                    = Column(Boolean, default=False, nullable=False)
    picked_up_at                                   = Column(DateTime(timezone=True), nullable=True)
    delivered                                       = Column(Boolean, default=False, nullable=False)
    delivered_at                                      = Column(DateTime(timezone=True), nullable=True)

    notes                                               = Column(Text, nullable=True)

    created_at                                            = Column(DateTime(timezone=True), server_default=func.now())

    route_detail = relationship("RouteDetail", back_populates="items")

    __table_args__ = (
        Index("ix_si_detail_id",     "route_detail_id"),
        Index("ix_si_package_code",  "package_code"),
        UniqueConstraint("route_detail_id", "line_number", name="uq_shipment_item_line"),
    )

    def __repr__(self):
        return f"<ShipmentItem #{self.line_number} {self.description} x{self.quantity}>"


# ─── DELIVERIES (Proof of Delivery) ──────────────────────────────────────────

class Delivery(Base):
    """
    Confirmación de entrega en una parada.
    Almacena la prueba de entrega: firma digital, foto, QR o PIN.
    Una parada completada tiene exactamente una Delivery.

    Soporta dos modelos de parada en paralelo durante la transición:
    - stop_id          -> modelo legacy (tabla stops)
    - route_detail_id  -> modelo vigente (tabla route_details)
    Al menos uno de los dos debe estar presente (ck_delivery_has_target).
    """
    __tablename__ = "deliveries"

    id                  = uuid_pk()
    stop_id             = Column(UUID(as_uuid=True), ForeignKey("stops.id", ondelete="CASCADE"),
                                 nullable=True)
    route_detail_id     = Column(UUID(as_uuid=True), ForeignKey("route_details.id", ondelete="CASCADE"),
                                 nullable=True)
    driver_id           = Column(UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False)

    pod_type            = Column(Enum(PodType, values_callable=lambda obj: [e.value for e in obj]), nullable=False)

    # Firma digital (SVG/PNG en base64 o URL)
    signature_url       = Column(String(512), nullable=True)
    signer_name         = Column(String(255), nullable=True)
    signer_id_number    = Column(String(100), nullable=True)  # DNI/pasaporte del receptor

    # Foto de entrega
    photo_url           = Column(String(512), nullable=True)
    photo_url_2         = Column(String(512), nullable=True)  # foto adicional si se requiere

    # Escáner QR / código de barras
    scanned_code        = Column(String(255), nullable=True)
    scan_verified       = Column(Boolean, default=False, nullable=False)

    # PIN confirmación
    pin_code            = Column(String(10), nullable=True)
    pin_verified        = Column(Boolean, default=False, nullable=False)

    # Ubicación GPS al momento de la entrega
    delivery_lat        = Column(Float, nullable=True)
    delivery_lng        = Column(Float, nullable=True)
    location_accuracy_m = Column(Float, nullable=True)  # precisión GPS en metros

    # Tiempo
    delivered_at        = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    on_time             = Column(Boolean, nullable=True)
    delay_minutes       = Column(Integer, default=0, nullable=False)

    # Notas
    driver_notes        = Column(Text, nullable=True)
    recipient_notes     = Column(Text, nullable=True)

    # Validación por el cliente corporativo
    validated_by_client = Column(Boolean, nullable=True)  # None = pendiente revisión
    validated_at        = Column(DateTime(timezone=True), nullable=True)
    dispute_reason      = Column(Text, nullable=True)

    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    # Relaciones
    stop                = relationship("Stop",        back_populates="delivery")
    route_detail        = relationship("RouteDetail", back_populates="delivery")
    driver              = relationship("Driver")

    __table_args__ = (
        Index("ix_deliveries_stop_id",          "stop_id"),
        Index("ix_deliveries_route_detail_id",  "route_detail_id"),
        Index("ix_deliveries_driver_id",        "driver_id"),
        Index("ix_deliveries_delivered_at",     "delivered_at"),
        CheckConstraint("stop_id IS NOT NULL OR route_detail_id IS NOT NULL", name="ck_delivery_has_target"),
    )

    def __repr__(self):
        target = self.route_detail_id or self.stop_id
        return f"<Delivery target={target} type={self.pod_type} at={self.delivered_at}>"

# ─── EARNINGS ─────────────────────────────────────────────────────────────────

class Earning(Base):
    """
    Liquidación financiera del conductor por ruta completada.
    Desglose completo: tarifa bruta, comisión, costos y pago neto.
    Conectado a Stripe para el payout real.
    """
    __tablename__ = "earnings"

    id                  = uuid_pk()
    route_id            = Column(UUID(as_uuid=True), ForeignKey("routes.id"), unique=True, nullable=False)
    driver_id           = Column(UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False)
    company_id          = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)

    # Tarifa
    gross_pay           = Column(Numeric(10, 2), nullable=False)
    muevo_commission_pct= Column(Numeric(5, 2),  nullable=False)
    muevo_commission_amt= Column(Numeric(10, 2), nullable=False)

    # Costos del conductor
    fuel_cost           = Column(Numeric(8, 2), nullable=False)
    wear_cost           = Column(Numeric(8, 2), nullable=False)
    total_costs         = Column(Numeric(10, 2), nullable=False)

    # Ajustes
    sla_bonus           = Column(Numeric(8, 2), default=0.00, nullable=False)
    sla_penalty         = Column(Numeric(8, 2), default=0.00, nullable=False)
    tip_amount          = Column(Numeric(8, 2), default=0.00, nullable=False)

    # Neto final
    net_pay             = Column(Numeric(10, 2), nullable=False)
    margin_pct          = Column(Numeric(5, 2),  nullable=False)

    # Datos de la ruta para el recibo
    total_km            = Column(Numeric(8, 2),  nullable=True)
    total_stops         = Column(Integer,         nullable=True)
    completed_stops     = Column(Integer,         nullable=True)
    gas_price_used      = Column(Numeric(6, 3),   nullable=True)

    # Pago
    payment_status      = Column(Enum(PaymentStatus, values_callable=lambda obj: [e.value for e in obj]), default=PaymentStatus.PENDING, nullable=False)
    stripe_transfer_id  = Column(String(255), nullable=True)
    paid_at             = Column(DateTime(timezone=True), nullable=True)

    # Periodo de liquidación
    period_start        = Column(DateTime(timezone=True), nullable=True)
    period_end          = Column(DateTime(timezone=True), nullable=True)

    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    route               = relationship("Route",   back_populates="earnings")
    driver              = relationship("Driver",  back_populates="earnings")

    __table_args__ = (
        Index("ix_earnings_driver_id",      "driver_id"),
        Index("ix_earnings_company_id",     "company_id"),
        Index("ix_earnings_payment_status", "payment_status"),
        Index("ix_earnings_paid_at",        "paid_at"),
    )

# ─── DRIVER METRICS ───────────────────────────────────────────────────────────

class DriverMetric(Base):
    """
    Métricas de eficiencia del conductor por día.
    Permite generar los gráficos del panel financiero:
    tiempo activo vs muerto, ganancia por hora, km por ruta, etc.
    """
    __tablename__ = "driver_metrics"

    id                  = uuid_pk()
    driver_id           = Column(UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"),
                                 nullable=False)
    metric_date         = Column(DateTime(timezone=True), nullable=False)  # fecha del día

    # Actividad
    routes_completed    = Column(Integer, default=0, nullable=False)
    stops_completed     = Column(Integer, default=0, nullable=False)
    km_driven           = Column(Numeric(8, 2), default=0.00, nullable=False)
    active_minutes      = Column(Integer, default=0, nullable=False)
    idle_minutes        = Column(Integer, default=0, nullable=False)

    # Financiero del día
    gross_earned        = Column(Numeric(10, 2), default=0.00, nullable=False)
    net_earned          = Column(Numeric(10, 2), default=0.00, nullable=False)
    fuel_spent          = Column(Numeric(8, 2),  default=0.00, nullable=False)
    bonuses_earned      = Column(Numeric(8, 2),  default=0.00, nullable=False)
    penalties_incurred  = Column(Numeric(8, 2),  default=0.00, nullable=False)

    # KPIs calculados
    avg_net_per_hour    = Column(Numeric(8, 2), nullable=True)
    avg_net_per_km      = Column(Numeric(8, 4), nullable=True)
    on_time_rate        = Column(Numeric(5, 2), nullable=True)   # % de paradas a tiempo
    completion_rate     = Column(Numeric(5, 2), nullable=True)   # % rutas completadas

    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    driver              = relationship("Driver", back_populates="metrics")

    __table_args__ = (
        UniqueConstraint("driver_id", "metric_date", name="uq_driver_metric_date"),
        Index("ix_driver_metrics_driver_id", "driver_id"),
        Index("ix_driver_metrics_date",      "metric_date"),
    )
