"""
dispatcher.py — Algoritmo de agrupamiento automático de paradas en rutas.

Factores considerados:
  1. Geografía       — k-means sobre lat/lng (minimiza distancia entre paradas de una ruta)
  2. Capacidad       — peso_max_lbs y volumen_max_ft3 del vehículo seleccionado
  3. Ventana horaria — paradas se ordenan dentro de cada ruta por hora_limite

Flujo:
  plan_stops() → lista de grupos propuestos (sin tocar la DB)
  Cada grupo contiene las paradas ordenadas, el peso total, el volumen total,
  la hora_limite más temprana (que define cuándo debe salir el vehículo), y
  un color sugerido para mostrar en el mapa del portal.
"""

import math
import uuid
from typing import Optional
from dataclasses import dataclass, field
import numpy as np

# ── Paleta de colores para el mapa (uno por ruta) ────────────────────────────
ROUTE_COLORS = [
    "#F47B20",  # naranja Muevo
    "#3498DB",  # azul
    "#2ECC71",  # verde
    "#E74C3C",  # rojo
    "#9B59B6",  # violeta
    "#F1C40F",  # amarillo
    "#1ABC9C",  # teal
    "#E67E22",  # naranja oscuro
    "#34495E",  # gris pizarra
    "#E91E63",  # rosa
    "#00BCD4",  # cian
    "#8BC34A",  # verde lima
]


@dataclass
class Stop:
    """Parada individual tal como llega del CSV o del formulario manual."""
    # Identificación
    temp_id:         str   = field(default_factory=lambda: str(uuid.uuid4())[:8])
    destino:         str   = ""
    direccion:       str   = ""
    codigo_postal:   str   = ""
    lat:             float = 0.0
    lng:             float = 0.0

    # Carga
    peso_lbs:        float = 0.0
    volumen_ft3:     float = 0.0
    valor_declarado: float = 0.0

    # Tiempo
    hora_limite:     str   = "23:59"   # HH:MM

    # Contacto
    contacto:        str   = ""
    telefono:        str   = ""
    codigo_cliente:  str   = ""
    codigo_paquete:  str   = ""
    codigo_barras:   str   = ""
    codigo_qr:       str   = ""
    notas:           str   = ""

    # Auditoría
    source:          str   = "csv"     # "csv" | "manual"

    # Desglose de paquetes de esta parada (opcional).
    # Si viene vacío, la parada se trata como un único paquete
    # (comportamiento legacy). Cada entrada es un dict con las mismas
    # claves que StopInput a nivel paquete: package_code, barcode, qr_code,
    # weight_lbs, volume_ft3, declared_value.
    items:           list  = field(default_factory=list)


@dataclass
class PlannedRoute:
    """Ruta propuesta por el algoritmo — aún no persistida en DB."""
    route_index:    int
    color:          str
    stops:          list   # list[Stop] ordenadas por hora_limite
    total_peso:     float
    total_volumen:  float
    earliest_limit: str    # hora_limite más temprana del grupo
    centroid_lat:   float
    centroid_lng:   float


def _time_to_minutes(hhmm: str) -> int:
    """Convierte 'HH:MM' a minutos desde medianoche. Acepta valores vacíos."""
    try:
        h, m = hhmm.strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 23 * 60 + 59  # sin ventana horaria → al final


def _split_overweight_cluster(stops: list[Stop], peso_max: float, volumen_max: float, max_stops: int) -> list[list[Stop]]:
    """
    Divide un cluster que excede capacidad en sub-grupos respetando
    peso, volumen y cantidad de paradas, priorizando las de hora más temprana.
    """
    # Ordenar por hora_limite primero para respetar ventanas al dividir
    sorted_stops = sorted(stops, key=lambda s: _time_to_minutes(s.hora_limite))
    groups = []
    current = []
    current_peso = 0.0
    current_vol  = 0.0

    for s in sorted_stops:
        fits_peso = current_peso + s.peso_lbs    <= peso_max
        fits_vol  = current_vol  + s.volumen_ft3 <= volumen_max
        fits_count= len(current) < max_stops

        if current and (not fits_peso or not fits_vol or not fits_count):
            groups.append(current)
            current = []
            current_peso = 0.0
            current_vol  = 0.0

        current.append(s)
        current_peso += s.peso_lbs
        current_vol  += s.volumen_ft3

    if current:
        groups.append(current)

    return groups


def plan_stops(
    stops:            list[Stop],
    peso_max_lbs:     float,
    volumen_max_ft3:  float,
    max_stops_per_route: int = 12,
) -> list[PlannedRoute]:
    """
    Agrupa las paradas en rutas óptimas usando k-means geográfico +
    verificación de capacidad + ordenamiento por ventana horaria.

    Args:
        stops:               Lista de paradas a agrupar.
        peso_max_lbs:        Capacidad máxima de peso del vehículo.
        volumen_max_ft3:     Capacidad máxima de volumen del vehículo.
        max_stops_per_route: Límite de paradas por ruta.

    Returns:
        Lista de PlannedRoute ordenadas por hora_limite más temprana.
    """
    if not stops:
        return []

    # ── Caso trivial: todas las paradas caben en una sola ruta ───────────────
    total_peso = sum(s.peso_lbs    for s in stops)
    total_vol  = sum(s.volumen_ft3 for s in stops)

    if (total_peso <= peso_max_lbs and
        total_vol  <= volumen_max_ft3 and
        len(stops) <= max_stops_per_route):
        stops_sorted = sorted(stops, key=lambda s: _time_to_minutes(s.hora_limite))
        return [PlannedRoute(
            route_index=0, color=ROUTE_COLORS[0],
            stops=stops_sorted,
            total_peso=total_peso, total_volumen=total_vol,
            earliest_limit=stops_sorted[0].hora_limite if stops_sorted else "23:59",
            centroid_lat=sum(s.lat for s in stops) / len(stops),
            centroid_lng=sum(s.lng for s in stops) / len(stops),
        )]

    # ── Estimar k inicial basado en capacidad ────────────────────────────────
    k_by_peso   = math.ceil(total_peso / peso_max_lbs) if peso_max_lbs > 0 else 1
    k_by_vol    = math.ceil(total_vol  / volumen_max_ft3) if volumen_max_ft3 > 0 else 1
    k_by_count  = math.ceil(len(stops) / max_stops_per_route)
    k = max(k_by_peso, k_by_vol, k_by_count, 2)
    k = min(k, len(stops))  # no más clusters que paradas

    # ── K-means geográfico ───────────────────────────────────────────────────
    coords = np.array([[s.lat, s.lng] for s in stops])

    try:
        from sklearn.cluster import KMeans
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(coords)
    except Exception:
        # Fallback: asignación secuencial si sklearn falla
        labels = [i % k for i in range(len(stops))]

    # ── Agrupar por label ────────────────────────────────────────────────────
    raw_clusters: dict[int, list[Stop]] = {}
    for stop, label in zip(stops, labels):
        raw_clusters.setdefault(label, []).append(stop)

    # ── Verificar capacidad de cada cluster y dividir si excede ─────────────
    final_groups: list[list[Stop]] = []
    for cluster_stops in raw_clusters.values():
        cp = sum(s.peso_lbs    for s in cluster_stops)
        cv = sum(s.volumen_ft3 for s in cluster_stops)
        cc = len(cluster_stops)

        if cp <= peso_max_lbs and cv <= volumen_max_ft3 and cc <= max_stops_per_route:
            final_groups.append(cluster_stops)
        else:
            # Dividir el cluster en sub-grupos que respeten capacidad
            sub_groups = _split_overweight_cluster(
                cluster_stops, peso_max_lbs, volumen_max_ft3, max_stops_per_route
            )
            final_groups.extend(sub_groups)

    # ── Ordenar grupos por hora_limite más temprana (rutas urgentes primero) ─
    def group_earliest(group: list[Stop]) -> int:
        return min(_time_to_minutes(s.hora_limite) for s in group)

    final_groups.sort(key=group_earliest)

    # ── Construir PlannedRoute por cada grupo ────────────────────────────────
    planned = []
    for idx, group in enumerate(final_groups):
        stops_sorted = sorted(group, key=lambda s: _time_to_minutes(s.hora_limite))
        tp = sum(s.peso_lbs    for s in group)
        tv = sum(s.volumen_ft3 for s in group)
        planned.append(PlannedRoute(
            route_index=idx,
            color=ROUTE_COLORS[idx % len(ROUTE_COLORS)],
            stops=stops_sorted,
            total_peso=tp,
            total_volumen=tv,
            earliest_limit=stops_sorted[0].hora_limite if stops_sorted else "23:59",
            centroid_lat=sum(s.lat for s in group) / len(group),
            centroid_lng=sum(s.lng for s in group) / len(group),
        ))

    return planned


def planned_route_to_dict(route: PlannedRoute) -> dict:
    """Serializa un PlannedRoute a dict JSON-serializable para la API."""
    return {
        "route_index":    route.route_index,
        "color":          route.color,
        "earliest_limit": route.earliest_limit,
        "total_peso_lbs": round(route.total_peso,    2),
        "total_vol_ft3":  round(route.total_volumen, 2),
        "stop_count":     len(route.stops),
        "centroid_lat":   round(route.centroid_lat, 6),
        "centroid_lng":   round(route.centroid_lng, 6),
        "stops": [
            {
                "temp_id":         s.temp_id,
                "destino":         s.destino,
                "direccion":       s.direccion,
                "codigo_postal":   s.codigo_postal,
                "lat":             s.lat,
                "lng":             s.lng,
                "peso_lbs":        s.peso_lbs,
                "volumen_ft3":     s.volumen_ft3,
                "hora_limite":     s.hora_limite,
                "contacto":        s.contacto,
                "telefono":        s.telefono,
                "codigo_cliente":  s.codigo_cliente,
                "codigo_paquete":  s.codigo_paquete,
                "codigo_barras":   s.codigo_barras,
                "codigo_qr":       s.codigo_qr,
                "notas":           s.notas,
                "valor_declarado": s.valor_declarado,
                "source":          s.source,
                "items":           s.items,
            }
            for s in route.stops
        ],
    }
