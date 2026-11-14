"""
Lee el esquema real desde los modelos (models.py) y devuelve, por tabla, sus columnas con el
formato del diccionario de datos: tipo, PK, FK, nulabilidad y notas.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import models
from models import Base


def _clases_por_tabla() -> dict:
    out = {}
    for _, obj in vars(models).items():
        if inspect.isclass(obj) and issubclass(obj, Base) and obj is not Base:
            tabla = getattr(obj, "__tablename__", None)
            if tabla:
                out[tabla] = obj.__name__
    return out


def _tipo(col) -> str:
    t = col.type
    nombre = type(t).__name__
    if nombre == "Enum":
        enum_class = getattr(t, "enum_class", None)
        return f"ENUM({enum_class.__name__ if enum_class else getattr(t, 'name', '?')})"
    if nombre in ("String", "VARCHAR"):
        largo = getattr(t, "length", None)
        return f"String({largo})" if largo else "String"
    if nombre == "UUID":
        return "UUID"
    if nombre in ("Numeric", "DECIMAL"):
        return "Numeric"
    if nombre in ("DateTime", "TIMESTAMP"):
        return "DateTime"
    return nombre


def _notas(col) -> str:
    if col.primary_key and _tipo(col) == "UUID":
        return "Clave primaria (UUID v4)"
    notas = []
    for fk in col.foreign_keys:
        notas.append(f"Referencia a {str(fk.target_fullname).split('.')[0]}")
    if col.unique:
        notas.append("Valor único")
    if col.server_default is not None:
        sd = col.server_default
        if type(sd).__name__ == "Identity":
            notas.append("Autoincremental (identidad)")
        else:
            arg = str(getattr(sd, "arg", sd)).lower()
            notas.append("Default: ahora (server)" if ("now" in arg or "timestamp" in arg) else f"Default (server): {getattr(sd, 'arg', sd)}")
    if col.default is not None:
        arg = col.default.arg
        if callable(arg):
            notas.append("Default: func.now" if "now" in getattr(arg, "__name__", "") else f"Default: {getattr(arg, '__name__', 'callable')}")
        else:
            notas.append(f"Default: {arg}")
    return " · ".join(notas) if notas else "—"


def esquema() -> dict:
    clases = _clases_por_tabla()
    tablas = []
    for nombre, tabla in sorted(Base.metadata.tables.items()):
        columnas = []
        for col in tabla.columns:
            columnas.append({
                "name": col.name,
                "type": _tipo(col),
                "pk": bool(col.primary_key),
                "fk": bool(col.foreign_keys),
                "nullable": bool(col.nullable),
                "notes": _notas(col),
            })
        tablas.append({"table": nombre, "class": clases.get(nombre, "?"), "num_cols": len(columnas), "cols": columnas})
    return {"num_tables": len(tablas), "tables": tablas}
