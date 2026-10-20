# Muevo — Resumen del proyecto

**Plataforma B2B2B de logística para el mercado hispano de EE.UU.**
Documento vivo — última actualización: 20 de octubre de 2026

---

## 1. Estructura del proyecto (rutas locales)

```
C:\Claude\
├── muevo-backend\        ← Backend FastAPI + PostgreSQL
│   ├── main.py            (API principal — todos los endpoints)
│   ├── models.py           (modelos SQLAlchemy)
│   ├── dispatcher.py       (algoritmo k-means de agrupamiento)
│   ├── warehouse.html      (portal warehouse — archivo único)
│   ├── transport.html      (portal empresa de transporte)
│   ├── admin.html          (portal de administración de Muevo — versión mínima: compañías, perfiles, historial de perfiles, auditoría)
│   ├── login.html          (login unificado)
│   ├── alembic\versions\   (34 migraciones — 001 a 034)
│   ├── seed_initial_data.py (bootstrap: holding→company→warehouse→usuario admin)
│   ├── seed_security_extensions.py (módulos/permisos nuevos + asigna perfiles)
│   ├── seed_test_profile_users.py (1 usuario de prueba por cada perfil de seguridad del warehouse)
│   ├── seed_geography.py   (EE.UU., 50 estados y sus 29.537 ciudades desde data\us_geography.json; completa ciudad de paradas existentes)
│   ├── seed_transport_profiles.py (perfiles RBAC de transporte + 3 usuarios de prueba de transporte)
│   ├── reset_operational_data.py (borra lotes/rutas/paradas/ofertas/clientes de prueba — no toca tenant/usuarios/RBAC)
│   ├── test_profile_history.py (prueba de punta a punta del historial de cambios de perfil — corre en una transacción que se revierte)
│   ├── seed_demo_data.py   (datos de demo — 8 lotes, 16 rutas, cubre cada estado de negociación)
│   ├── fix_*.py            (scripts de corrección de datos puntuales)
│   ├── docs\muevo.md       (este documento — vive en el repo desde octubre 2026)
│   ├── Muevo_Diccionario_de_Datos.docx / diccionario_datos_muevo.pdf (diccionario de datos — 42 tablas)
│   ├── .vscode\            (launch.json/settings.json/extensions.json — debug F5 con uvicorn)
│   ├── .venv\              (entorno virtual Python)
│   └── .env                (variables de entorno — no versionado)
│
└── muevo\                 ← App móvil Expo Router
    ├── app\
    │   ├── (warehouse)\    (grupo de rutas del portal warehouse)
    │   ├── (driver)\       (código de la app Drive — existente, no tocar)
    │   └── login.tsx        (login compartido, detecta portal y redirige)
    ├── hooks\useWarehouseApi.ts
    ├── constants\Colors.ts
    └── components\warehouse\
```

Ambas carpetas se movieron de `C:\muevo-backend` y `C:\muevo` a `C:\Claude\` en agosto 2026 para consolidar el espacio de trabajo. Si algún script o configuración interna todavía referencia la ruta antigua, actualizarla a la nueva base `C:\Claude\`.

---

## 2. Objetivo del proyecto

Muevo conecta tres actores en la cadena de distribución:

```
Warehouse (cliente corporativo)
        │
        │  crea lotes de rutas, negocia precios
        ▼
Empresa de transporte (carrier)
        │
        │  acepta/rechaza rutas, asigna vehículos
        ▼
Conductor (app Drive)
        │
        │  ejecuta la ruta, confirma entregas
        ▼
Destinatario final
```

El objetivo de negocio es que Muevo actúe como intermediario tecnológico —similar a un Uber Freight para el mercado hispano— cobrando una comisión sobre cada ruta ejecutada, sin poseer flota propia.

---

## 3. Arquitectura técnica

### 3.1 Stack

| Capa | Tecnología | Por qué |
|---|---|---|
| Backend | FastAPI + Python 3.13 | Async nativo, tipado con Pydantic, documentación automática (Swagger), curva de aprendizaje baja para iterar rápido |
| ORM | SQLAlchemy (síncrono) | Se eligió síncrono sobre async por simplicidad — el volumen actual no justifica la complejidad de sesiones async, y facilita debugging |
| Base de datos | PostgreSQL | Necesidad de relaciones complejas (holdings → companies → warehouses → routes), soporte nativo de ENUM, JSON, e índices parciales |
| Migraciones | Alembic | Versionado de schema reproducible; cada cambio de negocio queda documentado como migración numerada |
| Portales web | HTML + CSS + JS vanilla | Cero build step, cero dependencias de compilación — prioriza velocidad de iteración sobre un producto en etapa de validación de mercado por encima de arquitectura de frontend "correcta" |
| App móvil | React Native + Expo Router | Un solo codebase para iOS/Android; Expo Router permite convivencia de features (Drive existente + Warehouse nuevo) sin conflictos de routing |
| Mapas | Leaflet.js (CDN) | Gratuito, sin API key, suficiente para visualización de rutas sin necesitar Google Maps |
| Gráficos | Canvas 2D nativo | Se evitó Chart.js/D3 para no agregar una dependencia de 200kb+ cuando los gráficos requeridos (torta, barras) son simples; permite control total sobre efectos de hover/3D |

### 3.2 Por qué HTML vanilla en vez de un framework

Decisión consciente en una etapa temprana: los portales (`warehouse.html`, `transport.html`, `admin.html`, `login.html`) son archivos únicos autocontenidos. Esto significa:

- **Pro:** cero fricción de deploy (se sirven como archivos estáticos desde FastAPI), cero problemas de versiones de npm, cualquier desarrollador puede abrir el archivo y entender el flujo completo sin conocer un framework específico.
- **Contra reconocido:** los archivos son grandes (algunos superan las 5000 líneas). Esto es una deuda técnica aceptada deliberadamente a cambio de velocidad de iteración en fase de validación. Si el producto escala, es candidato a migrar a React/Vue con componentización real.

### 3.3 Modelo de datos — decisiones clave

**Holding → Company → Warehouse (multi-tenant)**
Se modeló así porque un mismo cliente corporativo (holding) puede operar múltiples marcas/compañías, y cada compañía puede tener múltiples almacenes físicos. Los permisos (`holding_user_companies`) son granulares por compañía, no globales — un usuario puede tener `can_operate` en una compañía y solo `can_view` en otra.

**Toda ruta pertenece a un lote — sin excepción**
Regla de negocio explícita: incluso una ruta individual sin agrupamiento vive dentro de un lote de una sola ruta. Esto simplifica el modelo de estados (el lote es la unidad de negociación, aprobación y facturación) y evita tener dos jerarquías paralelas de estado (ruta suelta vs. ruta en lote). Se implementó con:
- Detección automática de "rutas huérfanas" en el endpoint de listado, que las envuelve en un lote on-the-fly si se detectan
- Validación en la creación de lotes que impide que una ruta pertenezca a dos lotes simultáneamente

**`batch_number` independiente de `route_number`**
Las rutas ya tenían un identificador (`ORL-fecha-random`) heredado del sistema de despacho. Los lotes necesitaban su propio correlativo legible (`ORL-LOT-0001`) para uso humano en conversaciones con el cliente — un número de ruta con fecha+random no es memorizable ni comunicable por teléfono.

**`vehicle_type` y `service_type` a nivel de lote, no de ruta**
Inicialmente se intentó inferir el tipo de vehículo/servicio agregando los valores de las rutas del lote (mostrando "Múltiple" si diferían). Se corrigió porque el tipo de vehículo se decide en el Paso 1 del planificador de lotes — es una decisión que se toma una vez por lote, no por ruta, así que vive como columna directa en `route_batches`.

**Comisión Muevo: 12% → 5%**
Cambio de política de negocio. Se migró el default en `ServiceType`, se corrigieron todos los cálculos hardcodeados (seed, importación CSV, confirmación de lotes) y se documentó explícitamente en el sistema de negociación de precios.

**`codigo_cliente` de texto libre, no FK**
El código de cliente que el warehouse asigna a cada parada (ej: `CLI-001`) es texto libre, no una referencia a la tabla `batch_clients`. Decisión deliberada: el warehouse puede usar códigos internos de su propio ERP que no necesariamente coinciden con un registro formal en Muevo. La propagación automática (parada → ruta → lote) solo ocurre cuando todas las paradas/rutas comparten el mismo código; si son mixtas, el campo queda `NULL` en vez de forzar un valor incorrecto.

---

## 4. Funcionalidades implementadas

### 4.1 Autenticación y multi-tenancy
- JWT con expiración de 24hs
- Modelo holding-centric: usuarios pertenecen al holding, no a una compañía específica
- Selector de compañía en login para usuarios con acceso a múltiples compañías
- Sistema de perfiles y permisos granulares (`modules` × `actions` = `permissions`, agrupados en `profiles`)
- **El sistema de permisos ya está conectado a los endpoints reales** (antes existía el catálogo en la base pero ningún endpoint lo consultaba — se detectó y se cerró en agosto 2026):
  - `require_permission(user, db, module, action, company_id)` en `main.py` es el punto único de chequeo — se agregó a 24 endpoints de escritura (Tarifas, Clientes, Incidencias, Negociación de precios, Recepción, Inventario, Mensajes, aprobar/anular lotes)
  - **14 módulos** en el catálogo (8 originales + `incidents`/`clients`/`pricing`/`receptions`/`inventory`/`messages` agregados para cubrir las funcionalidades más nuevas), **6 perfiles**: `super_admin`, `company_admin`, `warehouse_admin`, `operator`, `viewer`, `billing_manager`
  - Un `HoldingUserProfile` define el alcance: `company_id = NULL` aplica a todo el holding, `company_id = X` solo a esa compañía. Los recursos globales (Tarifas, catálogo de mensajes, tipos de incidencia) solo aceptan una asignación de todo el holding — un `company_admin` acotado a una compañía no puede tocarlos, aunque su perfil nominalmente incluya el permiso
  - **Diseño deliberado "fail-open"**: un usuario sin ningún perfil asignado todavía pasa el chequeo (no rompe cuentas no migradas) — solo bloquea cuando el usuario tiene un perfil asignado que explícitamente no incluye ese permiso
  - `super_admin` del holding siempre pasa, sin excepción
  - **Cómo se sabe a qué portal pertenece un usuario** (`resolve_user_portal()`): por pertenencia a tablas, no por `users.role`. Orden de prioridad: admin de Muevo (`role = muevo_admin`) → `transport_company_admins` → `holding_users` → `company_admins` (legacy) → `drivers`. Alguien que esté en `transport_company_admins` y también en `holding_users` entra siempre al portal de transporte
  - **Admin de Muevo** (octubre 2026): `users.role = muevo_admin` es el personal administrativo de la plataforma (hoy `admin@muevo.app`). Entra a `/admin.html` (portal que antes se servía pero el archivo no existía; ahora hay una versión mínima con Compañías, Perfiles y Auditoría), no necesita `HoldingUser` y ve todos los holdings. `require_permission()` lo deja pasar siempre. Los endpoints de plataforma (crear/editar perfiles, crear/editar compañías y warehouses, asignar o quitar perfiles) exigen `require_platform_admin()`: admin de Muevo **o** super-admin de un holding. Antes, crear o editar perfiles lo podía hacer cualquier usuario autenticado. Un admin de Muevo no opera dentro de un warehouse (los endpoints `/warehouse/*` siguen pidiendo `HoldingUser`)
  - **Perfiles de transporte** (octubre 2026): 4 perfiles nuevos (`transport_admin`, `transport_dispatcher`, `transport_billing`, `transport_viewer`) y un módulo nuevo `fleet`. Se asignan por el mismo mecanismo que el warehouse, `holding_user_profiles`: cada usuario de transporte tiene además un `HoldingUser` (sin acceso a ninguna company) solo para colgar su perfil. Por eso `resolve_user_portal()` revisa `transport_company_admins` **antes** que `holding_users`. `require_transport_permission()` (fail-open como `require_permission()`) protege 6 endpoints: contraoferta y aceptar precio (`pricing.create`), aceptar ruta (`routes.approve`), rechazar ruta (`routes.cancel`), agregar vehículo (`fleet.create`) y facturación (`billing.view`). Los GET de lectura siguen abiertos. Sembrado con `seed_transport_profiles.py`. Se probó una tabla `profile_users` aparte y se descartó para tener un solo mecanismo de perfiles. Efecto secundario: los usuarios de transporte aparecen en el listado de usuarios del holding. De paso se corrigió que agregar un vehículo fallaba siempre (`VehicleType` sin importar en `main.py`)
  - Sembrado con `seed_security_extensions.py` (idempotente) — verificado contra la base real: usuario sin perfil pasa, perfil `viewer` bloquea creación pero permite ver, perfil `company_admin` puede operar en su compañía pero no toca catálogos globales

### 4.1.1 Conductor por ruta, no por vehículo (octubre 2026, migración `031_driver_per_route`)
`vehicles.driver_id` se eliminó. Antes el único vínculo conductor-vehículo era esa columna, así que un vehículo tenía un conductor fijo. Ahora:
- **`route_headers.driver_id`** asigna el conductor a cada ruta. La migración copió los datos existentes antes de borrar la columna (4 rutas conservaron su conductor).
- **`drivers.transport_company_id`** es nuevo: sin el vehículo, el conductor perdía su vínculo con la empresa de transporte. De ahí salen el listado de conductores de la empresa y las rutas que ve la app del conductor.
- **Aceptar una ruta** (`POST /transport/routes/{id}/accept`) acepta `vehicle_id` y `driver_id` opcionales; el conductor debe ser de la empresa. El modal de `transport.html` tiene un selector de vehículo y otro de conductor. Nuevo endpoint `GET /transport/drivers`.
- **Flota:** el conductor que se muestra para un vehículo es el de su ruta en curso o asignada; un vehículo sin ruta aparece sin conductor. El tracking de flota solo incluye vehículos con una ruta en curso.
- **Pendiente:** un conductor puede estar asignado a varias rutas a la vez; no hay validación de solapamiento de horarios (el modal solo deshabilita a quien ya está en una ruta en curso).

### 4.1.2 Geografía: país > estado > ciudad (octubre 2026, migración `032_geography`)
Tablas `countries`, `states` y `cities` para armar rutas por zona. Antes país/estado/ciudad eran texto libre en `companies`, `warehouses`, `transport_companies` y `route_details`, y en los datos de demo las paradas tenían ciudad y estado vacíos.
- **Datos cargados** (`seed_geography.py`, desde `data/us_geography.json`, generado de los códigos postales de GeoNames, licencia CC BY 4.0): EE.UU., sus 50 estados y 29.537 ciudades de todos los estados. Son los lugares postales, o sea todo lo que tiene código postal, no solo municipios (por eso hay barrios y localidades además de ciudades). Cada estado tiene entre cientos y unas 1.800 entradas; Pensilvania (1.786), Nueva York (1.610) y Texas (1.469) son los que más tienen. Washington D.C. y los territorios no están cargados.
- **`cities.zip_codes`** guarda los códigos postales de cada ciudad, así que la ciudad de una parada se resuelve por su zip. `name_normalized` unifica variantes ("Saint Petersburg" y "St. Petersburg", mayúsculas, acentos).
- **`resolve_city()`** (en `main.py`) resuelve la ciudad de una parada en este orden: código postal, nombre de ciudad + estado, y por último la ciudad más cercana a lat/lng (hasta 40 km; compara contra el centro de cada lugar postal, así que cerca de un límite puede elegir el lugar vecino. El zip es lo confiable). Al guardar una parada nueva, una ciudad que no existe dentro de un estado conocido se crea sola.
- **`route_details.city_id` y `warehouses.city_id`** apuntan a `cities`; los textos `city`/`state` se conservan y ahora se completan. La migración de datos dejó las 55 paradas existentes con ciudad (Orlando y Altamonte Springs).
- **Armado de rutas por zona:** `POST /warehouse/batches/plan` acepta `agrupar_por` (`ciudad`, `estado` o `pais`) y planifica por separado dentro de cada zona; cada ruta propuesta trae su `zona` (ciudad/estado/país). Las paradas que no se pueden ubicar quedan en un grupo aparte. Sin `agrupar_por` funciona como antes. Las paradas aceptan `ciudad` y `estado` opcionales.
- **Consulta:** `GET /geo/countries`, `/geo/states` y `/geo/cities?state=FL&q=ora`.
- **Pendiente:** la pantalla de carga de lotes de `warehouse.html` todavía no expone `agrupar_por` ni muestra la zona de cada ruta.

### 4.2 Dispatcher automático de rutas
- Algoritmo k-means geográfico (sklearn) que agrupa paradas sueltas en rutas óptimas
- Respeta capacidad de peso/volumen del vehículo y máximo de paradas configurables
- Fallback a asignación secuencial si sklearn no está disponible

### 4.3 Gestión de lotes (route_batches)
- Flujo de estados: `draft → approved → partially_accepted/fully_accepted → partially_completed → completed | closed_with_incidents | cancelled`
- Dashboard con matriz filtrable por estado, gráficos de torta (estados de lote) y barras (estados de ruta) con efectos de iluminación interactiva al hacer hover
- Detalle de lote con sub-header (tipo de servicio, tipo de vehículo, fecha estimada de entrega), bloques de resumen (estado de rutas, financiero, carga total) y matriz de rutas
- **Paradas multi-ítem**: el CSV y la carga manual agrupan automáticamente filas/paquetes con el mismo `destino+dirección` en una sola parada con N ítems (`ShipmentItem`), en vez de crear una parada duplicada por cada paquete
- **CRUD de rutas y paradas dentro de un lote en borrador** — agregar, editar y eliminar rutas/paradas después de creado el lote, con un único guardrail: solo mientras `batch.status == draft`. Una vez aprobado el lote es fijo (coherente con que ya se ofertó a transporte)
- **Validación real de datos** en la carga de paradas (CSV y manual), server-side y client-side: campos obligatorios, rangos de lat/lng, formato de hora, valores numéricos no negativos — reemplaza la validación casi nula que tenía la primera versión del importador
- **Límite de tamaño/filas de CSV configurable por compañía** (`Company.max_csv_rows` / `max_csv_file_size_mb`, ambos nullable) con fallback a un default global (`DEFAULT_MAX_CSV_ROWS`/`DEFAULT_MAX_CSV_FILE_SIZE_MB` en `main.py`) — evita que una compañía con volumen atípico necesite un cambio de código
- **`zip_code`/`lat`/`lng` en cada parada** (`route_details`), poblados por geocodificación (Nominatim/OpenStreetMap) en los datos de demo — visibles en el detalle de ruta y usados por el mapa
- **Barra de progreso durante la generación del lote** (Paso 1 "Generar agrupamiento" y Paso 3 "Crear lote" del planificador): como el servidor responde con un único request/response y no reporta progreso real, la barra anima hasta un tope del 92% durante una duración estimada según la cantidad de paradas y recién salta a 100% cuando la respuesta llega — evita prometer un % que no se sostiene si el servidor tarda más de lo esperado
- **Anular un lote aprobado exige un motivo** — cancelar un lote en `draft` sigue sin requerirlo (todavía no se ofertó a nadie), pero pasar de `approved` a `cancelled` obliga a completar un motivo (`RouteBatch.cancellation_reason`/`cancelled_at`), que queda visible en el detalle del lote y se incluye en el email de notificación al cliente
- **Aprobar un lote valida que todas sus rutas tengan precio distinto de cero** (`gross_pay != 0`) — si alguna ruta todavía no cerró su negociación, la aprobación se rechaza y lista los números de ruta pendientes de precio, en vez de dejar ofertar a transporte una ruta sin tarifa

### 4.3.1 Detalle de ruta (`openRoute()` en warehouse.html)
Reorganizado en agosto 2026 para seguir el mismo patrón que el detalle de lote (header con acciones → franja de datos rápidos → cards de resumen → contenido principal → matriz ancha), eliminando duplicación de badges de estado y de cifras financieras que tenía la versión anterior:
- **Cards de resumen + mapa lateral**: a la izquierda, en grilla 2×2, Progreso general / Estado de las paradas / Resumen financiero / "Detalle de carga" (paradas + paquetes + libras + ft³ volumen fusionados en una sola card, solo si la ruta tiene envío asociado). A la derecha, el mapa de ruta como panel alto (`min-height:280px`), no como elemento de ancho completo.
- **Matriz de paradas** reemplaza la tabla anterior, con columnas: Cliente, Dirección, Hora estimada, Fecha entregada, Hora entrega, Volumen, Peso, Cant. productos, Foto, Estatus.
- **Gap de datos conocido**: "Fecha entregada" se aproxima con la fecha de la ruta (no hay timestamp real por parada), y "Hora entrega" / "Foto" quedan en placeholder porque el modelo `Delivery` (`delivered_at`, `photo_url` en [models.py](muevo-backend/models.py)) todavía no se expone en `serialize_stop`/`serialize_route_detail` de `main.py`. Si se necesita el dato real, hay que sumarlo a esos serializers.
- El modal de "Detalle del envío" (`openShipmentModal`) perdió su único punto de entrada en la UI al sacar el link "Ver envío completo →" — el código sigue existiendo pero hoy es inalcanzable desde el detalle de ruta.

### 4.4 Escaneo de inventario (warehouse_inventory_items)
- Soporta 2 flujos: inventario previo → generar lote, o escaneo directo → agregar a lote en curso
- Parsea 3 formatos de código: JSON, pipe-delimited, o ID simple con lookup
- Deduplicación automática: escanear el mismo código dos veces incrementa `quantity` en vez de crear una parada duplicada — decisión tomada porque un pedido real puede llegar en múltiples cajas con el mismo código de cliente

### 4.5 Clientes del warehouse (batch_clients)
- CRUD completo + importación masiva por CSV
- Preferencias de notificación granulares por evento (cambio de estado de lote, cambio de parada, entrega confirmada, incidencia)
- Sistema de notificación por email vía SMTP configurable (con fallback a log en consola si no hay credenciales)
- Historial de lotes y métricas por cliente (total lotes, completados, costo total)

### 4.6 Incidencias
- Catálogo configurable de tipos (`incident_types`) con severidad y flag de si afecta el estado de la ruta automáticamente
- 20 tipos pre-sembrados en 4 niveles de severidad
- Flujo de resolución: `open → under_review → resolved | unresolved`
- ABM completo del catálogo desde el portal (sin necesidad de acceso a base de datos)

### 4.7 Negociación de precios (route_price_offers)
- Decisión de diseño: negociación por ruta individual (no por lote), con múltiples rondas de contraoferta
- Historial completo e inmutable — las ofertas nunca se borran, se marcan como `superseded`/`rejected`/`selected`/`accepted`
- El warehouse nunca ve la comisión de Muevo ni el neto del transportista — solo la tarifa bruta que paga (decisión de negocio: esa información es confidencial entre Muevo y el transportista)
- **Precio inicial sugerido automáticamente** (`calculate_suggested_price()` en `main.py`): `precio_servicio` (base fija del tipo de servicio) + `precio_por_km`×distancia + `precio_por_lb`×peso + `precio_por_ft3`×volumen, acotado a `[importe_minimo, importe_maximo]`. Todavía no incorpora demanda (disponibilidad de transportistas, histórico de aceptación) porque no existe esa métrica en el sistema
- Esas tarifas por unidad (`precio_por_km`/`precio_por_lb`/`precio_por_ft3`) son un parámetro por tipo de servicio, no un valor hardcodeado — administrable desde el portal en la sección **"Tarifas"** (ABM completo: crear tipo de servicio, editar precio base/tarifas/comisión/rango, activar/desactivar), con el mismo patrón de catálogo configurable que `incident_types`
- **`gross_pay` se fija con esa fórmula apenas se crea la ruta** (septiembre 2026, `_apply_formula_price()` en `main.py`, enganchado en `confirm_batch` y `add_route_to_batch`) — antes arrancaba en `$0.00` y solo dejaba de estarlo si se completaba una negociación entera de punta a punta. Esto es lo que de verdad hace cumplible la validación "toda ruta del lote necesita precio distinto de cero para aprobar" (ver [4.3](#43-gestión-de-lotes-route_batches)): la mayoría de las rutas nunca pasan por el marketplace y ahora tienen precio de todos modos. No recalcula solo si se editan paradas/peso después de creada la ruta (fuera de alcance por ahora)
- **Aprobar el lote abre el marketplace automáticamente** (septiembre 2026, `_open_route_marketplace()`, enganchado en `change_batch_status` cuando `status == "approved"`) — hasta ahora, aunque la ruta ya tuviera precio por la fórmula, un transportista no podía pujar hasta que el warehouse abriera manualmente cada ruta con "Sugerir precio". El flujo completo quedó así: **generar rutas** (nacen con precio de fórmula, `negotiation_status = none`) → **aprobar el lote** (usuario con `batches.approve`, ya validaba precio ≠ 0; ahora además pone `negotiation_status = suggested` y publica ese `gross_pay` como ask inicial, sin acción manual extra) → **marketplace** (N transportistas pujan en paralelo, igual o distinto al precio inicial) → **el warehouse elige una oferta** (`select-offer`, pasa a "en holding") → **holding aprueba** (`holding-approve`) → la ruta queda **negociada** (`negotiation_status = accepted`) con el precio que se haya pactado. El guard `_open_route_marketplace()` no pisa una negociación que ya estaba en curso o cerrada antes de aprobar el lote (solo actúa si `negotiation_status == none`)

**Marketplace multi-transportista con aprobación de holding** (agosto 2026) — reemplazó el modelo original 1-a-1 (una ruta solo se negociaba con la empresa de transporte ya asignada):
- El warehouse sugiere un precio público (`suggest-price`) y la ruta queda abierta a pujas de **cualquier empresa de transporte activa** — no hace falta invitación previa (marketplace abierto, decisión tomada con el usuario en vez de una lista de transportistas invitados)
- Cada empresa de transporte tiene su **propio hilo de ofertas**, independiente del resto (`route_price_offers.transport_company_id`) — pueden pujar en paralelo sin verse ni pisarse entre sí; el warehouse ve todos los hilos, cada transportista solo ve el propio
- **Información del transportista junto a cada oferta** (septiembre 2026, extendido en `serialize_negotiation()`): antes de elegir una oferta el warehouse ve, por cada hilo, si está `is_verified`, rating, % a tiempo, rutas totales/completadas/rechazadas, cuántas rutas tuvo específicamente con esa company (`routes_with_us`, mismo cálculo que ya usaba `warehouse_list_transport_companies`), y datos de contacto/DOT/MC en un detalle expandible — para poder decidir con algo más que el monto
- El warehouse elige qué oferta aceptar (`select-offer/{transport_company_id}`) — esto **no cierra el trato**: las demás ofertas pendientes quedan `rejected` y la ruta pasa a estado `pending_holding_approval` ("en holding")
- La ruta queda en holding hasta que alguien con permiso `pricing.approve` de **alcance holding-wide** (`company_id = NULL` en `require_permission()` — hoy en la práctica `super_admin`, o cualquier perfil al que se le asigne ese alcance) la aprueba (`holding-approve`). Recién ahí se fija `gross_pay`, la comisión (5%), el neto estimado, y se asigna `route_headers.transport_company_id` al transportista ganador. No existe un endpoint de rechazo — mientras nadie con ese alcance apruebe, la ruta simplemente sigue pendiente
- **Handoff al flujo normal de transporte**: si la ruta estaba en `draft`, `holding-approve` también la pasa a `published` — sin esto, el transportista ganador nunca la vería en "Rutas ofrecidas" ni podría aceptarla/asignarle un vehículo (el resto del flujo de transporte exige `status == published`). Se detectó y se corrigió el mismo día que se implementó el marketplace — verificado con un test que corre la negociación completa y confirma que el ganador puede aceptar la ruta al final
- **Si el ganador rechaza la ruta después de la aprobación de holding, se reabre el marketplace para todos** (`POST /transport/routes/{id}/reject`, decisión de producto explícita): `negotiation_status` vuelve a `suggested`, `route.status` vuelve a `draft`, se limpian `transport_company_id`/`holding_approved_by`/`holding_approved_at`/`pending_offer_id`, y `gross_pay`/comisión/neto se resetean a cero. Cualquier empresa —incluida la que acaba de rechazar— puede volver a pujar sobre el mismo precio público; el historial no se borra, la oferta que había ganado queda marcada `accepted` como registro y las pujas nuevas se agregan a continuación en el mismo hilo
- `transport.html` no tenía **ninguna** interfaz de negociación (los endpoints existían pero no había UI que los consumiera) — se construyó desde cero una sección "Negociación" con el marketplace de rutas abiertas y el historial de ofertas propias
- Las rutas despachadas manualmente (el dispatcher asigna `transport_company_id` directo, sin pasar por `suggest-price`) no se ven afectadas — el flujo de negociación es opt-in
- **Pantalla "Ofertas por ruta" en `warehouse.html`** (septiembre 2026): overview de todas las negociaciones activas de la company en una sola tabla (tabs Abiertas a pujas / En holding / Cerradas / Todas), sin tener que abrir cada ruta individualmente — reutiliza el listado de rutas ya cargado en el cliente (`getActiveRoutes()`, que ya trae `negotiation_status`/`suggested_price`) y el mismo modal de negociación existente (`openNegotiationPanel`) al hacer clic en "Ver ofertas", sin backend nuevo
- **Widget de marketplace en el Dashboard de `transport.html`** (septiembre 2026): hasta ahora enviar una oferta requería ir a la pestaña "Negociación" — el Dashboard ahora muestra directamente hasta 3 rutas abiertas a pujas con "Aceptar precio"/"Contraofertar" in-line, reusando `getMarketplaceRoutes()`/`acceptMarketAsk()`/`sendMarketCounter()` ya existentes (`refreshMarketplace()` se extendió para refrescar el Dashboard además de la pestaña Negociación). El input de contraoferta arranca pre-llenado con el precio público actual (`current_ask`) en vez de vacío, tanto en el widget como en la pestaña Negociación
- **Dashboard de `transport.html` rediseñado para parecerse al del warehouse** (septiembre 2026): fila de 5 cards — Total rutas y Vehículos en línea (ambas con desglose por estado en el pie de la card), Ingreso neto acumulado, y dos gráficos interactivos (torta "Estados de rutas", barras 3D "Estado de flota": en ruta / disponible / desconectado / sin conductor). `setupPieChart`/`setupBarChart`/`shadeColor` se copiaron tal cual de `warehouse.html` (son genéricas: reciben canvas + datos + id de leyenda), no se extrajeron a un archivo compartido porque los portales son archivos únicos autocontenidos (ver [decisión de arquitectura](#3-arquitectura-técnica))
- **Rutas del Dashboard de transporte como matriz por estado, no bloques** (septiembre 2026): las tres listas de cards (ofrecidas / en curso / próximas) se reemplazaron por una sola tabla con filtros por estado (Todas / Ofrecidas / Programadas / En curso / Completadas / Canceladas, con conteo; se ocultan los estados sin rutas) — mismo patrón que la "Matriz de lotes" del warehouse. Las filas `offered` traen "Aceptar"/"Rechazar" inline; el resto abre el detalle. Se eliminaron `offeredRouteCard()`/`dashRouteCard()` al quedar sin uso. El aviso "Tenés N rutas esperando respuesta" se mantiene arriba de la matriz
- **Datos reales, no de demo, en el Dashboard de transporte** (septiembre 2026): dos bugs hacían que un usuario real viera datos falsos o vacíos — (1) `serialize_route_header()` en `main.py` nunca incluía `vehicle_id`, `vehicle_plate`, `driver_name`, `transport_company_name` ni `net_pay`, aunque ambos frontends ya los leían, así que toda ruta real mostraba "$0.00 neto" y "Sin vehículo asignado" sin importar la base (se agregó además la relación `RouteHeader.vehicle`, unidireccional); (2) el KPI "Vehículos en línea" leía el array demo `FLEET` sin pasar por `useRealData && REAL_FLEET ? REAL_FLEET : FLEET`, patrón que sí usa el resto del archivo

### 4.8 App móvil (Expo Router)
- Grupo de rutas `(warehouse)` conviviendo con el código existente de Drive sin modificarlo
- Dashboard, detalle de lote, listado de rutas, perfil
- Autenticación compartida vía `AsyncStorage`, detección de portal en el login que redirige según el rol

### 4.9 Recepción de mercancía — inbound (`receptions` / `reception_items`)
- Cubre dos orígenes con el mismo flujo unificado: mercancía nueva de proveedores/clientes del warehouse, y devoluciones de paquetes de rutas ya despachadas (`source_type = supplier | return`, con `origin_route_id` opcional para vincular la devolución a su ruta)
- Flujo de dos pasos, no un registro libre: primero se crea el manifiesto esperado (`Reception` + N `ReceptionItem`, estado `expected`), después se hace check-in físico ítem por ítem (`received` / `damaged`)
- Solo los ítems marcados `received` generan un `WarehouseInventoryItem` (disponible/`pending`) — mercancía dañada queda registrada en la recepción pero deliberadamente **no** se suma al inventario despachable
- Al cerrar la recepción (`POST .../complete`), cualquier ítem que siga `pending` pasa automáticamente a `missing` — no hay recepciones que queden en un limbo sin resolver
- Numeración correlativa propia (`ORL-REC-0001`) igual que `batch_number`, mismo patrón (`next_reception_number()`)
- Para devoluciones, el modal de alta puede precargar como ítems esperados las paradas no entregadas (`status != completed`) de la ruta de origen, reutilizando `GET /warehouse/routes/{id}`
- Este fue un ítem explícitamente pospuesto (ver historial de "Pendiente / próximos pasos") — se retomó y se implementó completo (modelo, migración 025, endpoints, UI) en agosto 2026

### 4.10 Auditoría (`audit_logs`) — septiembre 2026
No existía ningún mecanismo de historial de cambios — se pidió poder trackear cualquier movimiento en la base para sostener una auditoría (quién bajó un precio, quién borró un cliente, quién aprobó qué, cuándo).
- **Captura totalmente automática, sin tocar los ~126 endpoints uno por uno**: un listener global de SQLAlchemy (`before_flush`, en [models.py](muevo-backend/models.py)) inspecciona cada `insert`/`update`/`delete` que pasa por el ORM en cada `commit()` y genera su fila de `audit_logs` en la misma transacción — es atómico, si el cambio de negocio falla no queda un registro de auditoría huérfano
- Cada fila guarda `table_name`, `record_id`, `action`, `changed_by_user_id`, `company_id` (best-effort, si el registro lo tiene), y `changes` (JSONB): snapshot completo en insert/delete, diff `{"campo": {"old":…, "new":…}}` solo de lo que cambió en update
- **Quién hizo el cambio, sin cambiar ninguna firma de endpoint**: un middleware (`audit_user_context_middleware` en `main.py`) decodifica el JWT de cada request y lo deja en un `ContextVar` (`current_audit_user_id`, definido en `models.py`) que el listener lee al vuelo — best-effort, un token ausente o inválido no bloquea la request, solo deja el autor en `NULL` (ej. un script corrido a mano)
- **Filtro de ruido**: se detectó que `PATCH /api/v1/drivers/me/location` (ping de GPS del conductor, potencialmente cada pocos segundos) inundaría la tabla sin aportar nada a una auditoría real — un `update` de solo `drivers.current_lat/current_lng/last_location_at` no genera fila (el dato se sigue guardando igual, solo no se audita)
- Acceso exclusivamente holding-wide (mismo mecanismo que la aprobación de holding en la negociación de precios, ver [4.7](#47-negociación-de-precios-route_price_offers)): nuevo módulo RBAC `audit` con una sola acción `view`, otorgado solo al perfil `super_admin`, gateado con `require_permission(..., "audit", "view", company_id=None)`
- `GET /api/v1/admin/audit-logs` — filtros por `table_name`/`record_id`/`changed_by_user_id`/`date_from`/`date_to`, paginado, devuelve el usuario resuelto (nombre/email, no solo el UUID)
- Verificado de punta a punta: crear/editar/"borrar" (soft-delete real del sistema, `is_active=False`) un cliente genera las 3 filas esperadas con el diff correcto; el ping de GPS no genera fila; un usuario sin alcance holding-wide recibe 403
- **Limitación conocida y aceptada**: solo captura cambios que pasan por la `SessionLocal` de la app vía ORM — un `UPDATE`/`DELETE` corrido a mano contra Postgres (psql/pgAdmin) o los scripts sueltos (`fix_*.py`, seeds) que usan SQL crudo no queda registrado. Cubre el 100% de los endpoints de la API (ninguno muta con SQL crudo hoy), que es donde ocurre el movimiento real de negocio
- **Detalle menor**: en el snapshot de un `insert`, las columnas con `server_default` (ej. `created_at`, `updated_at`, algunos booleanos con default de Postgres) aparecen como `null` — Postgres las resuelve recién al ejecutar el INSERT real, después de que se arma el snapshot de auditoría. No afecta a `update`/`delete` (ahí se lee el valor ya resuelto)
- **Pantalla en el portal** (`warehouse.html`, sección "Auditoría" en el sidebar): tabla con filtros por tabla/ID de registro/rango de fechas, paginada, cada cambio expandible (`<details>`) para ver el diff completo. Sin gateo de visibilidad en el sidebar (el ítem del menú es visible para cualquiera, igual que "Aprobar (holding)" en la negociación de precios) — si el usuario no tiene alcance holding-wide, la pantalla muestra un mensaje claro en vez del error crudo del 403
- **Bug real encontrado y corregido**: `_audit_serialize()` solo sabía convertir `datetime` a texto — un campo `date` puro (ej. `route_headers.scheduled_date` al crear una ruta) rompía el `insert` a mitad de camino con un `TypeError` no capturado, tumbando la creación de rutas/lotes enteros. Se detectó al implementar el precio automático por fórmula (ver [4.3](#43-gestión-de-lotes-route_batches)) y probarlo contra la base real — quedó corregido para aceptar `date` además de `datetime`

- **Aclaración sobre DELETE/UPDATE masivos** (octubre 2026): la afirmación de arriba de que ningún endpoint escapa a la auditoría no era del todo cierta — `update_profile` y `update_holding_user` reemplazaban permisos y accesos a compañías con `db.execute(sa_delete(...))`, un DELETE masivo que no pasa por el listener, así que los permisos o accesos QUITADOS no quedaban registrados. Se cambiaron a borrar solo la diferencia objeto por objeto con `db.delete()` (ver [4.12](#412-historial-de-cambios-de-perfil-profile_change_history--octubre-2026)). Regla para endpoints nuevos: no usar `sa_delete`/`sa_update` sobre tablas que deban quedar auditadas

### 4.11 Notificaciones (`notifications`) — septiembre 2026
Todo el ciclo de negociación/despacho construido esta sesión era 100% "pull" — nadie se enteraba de nada salvo que volviera a abrir la pantalla. Lo único parecido a notificaciones que existía (`notify_batch_client`) es para el contacto externo del cliente del lote, no para los usuarios reales de los portales. Se pidió un sistema real, in-app + email, para ambos lados: ejemplo dado — al aprobar un lote/ruta, los transportistas se enteran.
- **In-app (campanita, polling cada 30s) + email** — no WebSocket nuevo (decisión explícita, para no tener que replicar/generalizar el `TrackingConnectionManager` con canales por transportista que hoy no existen). Cada notificación se persiste en `notifications` (para la campanita) y además dispara un email best-effort al mismo usuario reusando `send_email()`/`email_html()` (los mismos helpers de `notify_batch_client`, que ya loguean en consola en modo dev sin SMTP configurado)
- `type` es texto libre en vez de un enum de Postgres — evita una migración `ALTER TYPE` cada vez que se agregue un tipo de notificación nuevo (mismo criterio que `RouteHeader.dispatch_mode`)
- Tres helpers de fan-out en `main.py`: `notify_transport_company()` (a todos los `TransportCompanyAdmin` de una empresa), `notify_all_active_transport_companies()` (broadcast a todas las activas), `notify_company_admins()` (a `HoldingUserCompany` + `CompanyAdmin` legacy de una company, deduplicado) — todos construidos sobre `create_notification()`
- **Cobertura — todo el ciclo de negociación/despacho, no solo el caso puntual pedido** (confirmado con el usuario), simétrica en ambos portales:
  - **Hacia transportistas**: se aprueba un lote → abre el marketplace (`change_batch_status`, broadcast a todas las empresas activas); el warehouse selecciona tu oferta (`warehouse_select_offer`) o la de otro; holding aprueba tu ruta ganada (`holding_approve_negotiation`); se te ofrece un lote directo (`offer_batch`)
  - **Hacia el warehouse**: llega una oferta/contraoferta (`transport_counter_offer`/`transport_accept_price`); una negociación queda pendiente de holding (mismo `warehouse_select_offer`, notificación adicional informativa); un transportista acepta o rechaza un lote ofrecido directo (`respond_batch_item`); un transportista rechaza una ruta ya negociada y reabre el marketplace (`transport_reject_route`, ver [4.7](#47-negociación-de-precios-route_price_offers)) — este caso además vuelve a hacer el broadcast a todas las transport companies
- `GET /api/v1/notifications` (propias, `unread_only`/`limit`), `GET .../unread-count`, `POST .../{id}/read`, `POST .../read-all` — sin gate de permiso más allá de estar logueado, es inherentemente personal (`Notification.user_id == current_user.id`)
- **Campanita 🔔 en ambos portales** (topbar): badge de no leídas con polling cada 30s, dropdown con las recientes (título, cuerpo, tiempo relativo), click marca como leída y hace deep-link — en warehouse.html abre directo el panel de negociación de esa ruta (`openNegotiationPanel`); en transport.html, como la ruta puede no estar asignada todavía (sigue en marketplace abierto), navega a la vista "Negociación" en vez de intentar abrir una ruta que quizás no tiene cargada
- Verificado de punta a punta contra la base real: aprobar un lote notifica a las 2 empresas de transporte activas; una contraoferta notifica a los 7 admins de la company (todos los que tienen `HoldingUserCompany`/`CompanyAdmin` para esa compañía — incluye a los 6 usuarios de prueba por perfil); seleccionar oferta notifica ganador+perdedor+admins; holding aprueba notifica al ganador; rechazar una ruta ya negociada reabre y notifica a ambos lados; marcar como leída/leídas todas funciona. Confirmado también en el navegador real en ambos portales, incluido el deep-link desde la notificación hasta el panel de negociación exacto

### 4.12 Historial de cambios de perfil (`profile_change_history`) — octubre 2026
Se pidió controlar el cambio de perfil y mantener un historial legible, con persona, fecha, cambio, valor anterior y valor actual. `audit_logs` ya captura los cambios técnicos, pero no responde bien "¿quién le quitó este permiso al perfil operator?" ni dice qué usuario fue afectado.
- **Tabla `profile_change_history`** (migración `033_profile_change_history`): una fila por cambio con `changed_at`, persona (`changed_by_user_id` + copia de nombre y correo), `source` (`api` o `sistema`), `entity_type`, `change_type`, `description` (frase en español), `field_name`, `old_value` (valor anterior), `new_value` (valor actual), el perfil afectado (`profile_id`/`profile_code`), el usuario afectado (`target_user_id`/`target_user_email`), la empresa (`company_id`/`company_name`) y `details` (JSONB). Una columna `seq` (identidad) conserva el orden dentro de una transacción, donde `changed_at` empata en `audit_logs`
- **Qué vigila**: `profiles` (crear, cambiar nombre/descripción/estado, borrar), `profile_permissions` (permiso agregado o quitado), `holding_user_profiles` (perfil asignado, quitado, o cambiado de perfil o de alcance), `holding_users` (rol, super admin, estado activo) y `holding_user_companies` (acceso otorgado, quitado o con banderas cambiadas). Tipos de cambio: `profile_created/updated/deleted`, `permission_added/removed`, `profile_assigned/unassigned/assignment_changed`, `holding_user_created/updated/deleted`, `company_access_granted/revoked/changed`
- **Captura automática, sin tocar cada endpoint**: segundo listener `before_flush` (`_capture_profile_history` al final de `models.py`), independiente del de auditoría. Escribe en la misma transacción que el cambio, así que si el cambio se deshace, el historial también. Cubre también lo que hacen los scripts (`seed_*`), con `source = sistema` y persona vacía
- **Solo lectura**: un trigger de Postgres (`trg_profile_change_history_rows` y `_truncate`) rechaza UPDATE, DELETE y TRUNCATE. Sin llaves foráneas a propósito: el historial sobrevive aunque se borre el perfil o el usuario. El listener de auditoría ignora esta tabla para no duplicar cada fila
- **Endpoints**: `GET /api/v1/admin/profile-history` (filtros `profile_code`, `profile_id`, `target_user_email`, `changed_by_user_id`, `entity_type`, `change_type`, `date_from`, `date_to`, `limit`, `offset`; permiso `audit.view` de alcance holding-wide, igual que la auditoría) y `PATCH /api/v1/admin/users/profiles/{assignment_id}` (cambia el perfil o el alcance de una asignación en un solo paso; antes había que quitarla y volver a crearla; exige `require_platform_admin()`)
- **Cambio en dos endpoints existentes**: `update_profile` (permisos) y `update_holding_user` (accesos a compañías) ya no hacen DELETE masivo ni recrean todo: calculan la diferencia, borran solo lo que sobra objeto por objeto (`db.delete`), actualizan las banderas que cambian y agregan lo nuevo. Efecto: el historial y `audit_logs` registran solo lo que de verdad cambió, y los accesos que no cambian conservan `granted_by_id`/`granted_at`
- **Pantalla**: pestaña "Historial de perfiles" en `admin.html` (Fecha, Persona, Cambio, Valor anterior, Valor actual; filtros por perfil y usuario afectado)
- **Verificado**: `test_profile_history.py` llama a los endpoints reales con `TestClient` dentro de una transacción externa que se revierte (38 comprobaciones: crear/editar perfil, asignar, cambiar y quitar asignación, accesos a compañías, consulta con filtros, 403 para un `operator`, rechazo de UPDATE/DELETE/TRUNCATE y que el historial no se audita a sí mismo). Contra el `main.py` anterior (con DELETE masivo) la misma prueba falla 16 comprobaciones, justo en lo que no se registraba
- **Límites conocidos**: solo ve lo que pasa por el ORM (SQL crudo, psql o un DELETE masivo no pasan por el listener); el rol de la app es superusuario y puede desactivar el trigger, así que para endurecerlo hace falta un rol de aplicación sin ese permiso; el historial empieza el día de la migración (no hay datos anteriores)

### 4.13 Registro de empresas de transporte (`transport_applications`) — octubre 2026
Hasta ahora no había forma de que una empresa de transporte se registrara sola: entraban por los scripts de carga. Ahora hay un flujo con revisión de Muevo, porque el marketplace le muestra a los warehouses el sello de "verificada" y no puede ser autoservicio.
- **Formulario público** `register-transport.html` (enlazado desde `login.html`) y `POST /api/v1/transport/register`. Pide razón social, EIN (9 dígitos), al menos un número DOT o MC, póliza y vencimiento del seguro (debe ser futuro), servicios, contacto, ubicación (estado reconocido en `states`) y el administrador de la cuenta (correo y contraseña de 8+ caracteres). Rechaza duplicados de correo o EIN con un 409 genérico, y limita a 10 intentos por hora por IP (en memoria; se reinicia con el servidor)
- **Queda pendiente**: la solicitud se guarda en `transport_applications` con el hash de la contraseña, sin crear todavía ningún usuario ni empresa. Se avisa a todos los admins de Muevo (notificación en el portal y correo) y se le confirma al solicitante por correo
- **Revisión** en `admin.html`, pestaña "Solicitudes de transporte" (Pendientes / Aprobadas / Rechazadas / Todas). `GET /admin/transport-applications`, `POST .../{id}/approve` y `POST .../{id}/reject` exigen `require_platform_admin()`
- **Aprobar** crea en una sola transacción: la `TransportCompany` con `is_verified = true`, `verified_at` y `verified_by`; el `User` administrador con el hash que eligió el solicitante; su `TransportCompanyAdmin` (principal, con todos los permisos de la empresa); un `HoldingUser` y el perfil `transport_admin` (ver [4.1](#41-autenticación-y-multi-tenancy)). Le llega un correo y ya puede entrar al portal de transporte
- **Rechazar** exige un motivo, que se le envía al solicitante; puede volver a enviar una solicitud nueva
- **Seguridad, de paso**: `POST /auth/register` aceptaba el `role` que mandara quien se registraba, así que cualquiera podía crearse como `muevo_admin` (un rol que ahora abre `admin.html`); ahora siempre crea un conductor. Además `audit_logs` guardaba el `hashed_password` de los usuarios en `changes`; ahora esas columnas (`hashed_password`, `admin_password_hash`) se registran como `[oculto]`. Quedan 4 filas anteriores en `audit_logs` con hashes, que no se tocaron porque la auditoría es un registro que no se edita: borrarlas o enmascararlas es una decisión pendiente
- **Verificado**: contra la base real, 49 comprobaciones (validaciones, duplicados, permisos 403, aprobar y entrar al portal con el perfil, rechazar, auditoría sin hashes, límite de intentos) y el flujo completo en el navegador
- **Pendiente**: registro de warehouses (compañías) y de conductores por invitación de su empresa, con el mismo esquema de solicitud y revisión; subir documentos (póliza, DOT) en vez de solo los números; y que Muevo verifique el DOT/MC contra la FMCSA en lugar de revisarlos a mano

---

## 5. Decisiones de UX relevantes

- **Warehouse nunca ve cifras de comisión ni neto del transportista** — todas las vistas (detalle de ruta, detalle de lote, facturación) muestran únicamente la tarifa bruta.
- **Gráficos con iluminación por hover en vez de 3D estático** — se descartó el efecto de extrusión 3D permanente (visualmente cargado) a favor de un disco/barras planas que solo se "iluminan" al pasar el mouse, con atenuación del resto de los segmentos para dirigir la atención.
- **Toda tabla ancha usa `overflow-x:auto` con `min-width`** — en vez de comprimir columnas hasta ilegibilidad en pantallas angostas, se prefiere scroll horizontal.
- **Botón "Volver" con memoria de contexto** — al entrar a una ruta desde el detalle de un lote, el botón volver regresa a ese lote específico (no al dashboard general), pasando el `batchId` de origen como parámetro.

---

## 6. Estado actual (octubre 2026)

### Completado
- Backend: 34 migraciones Alembic aplicadas, 47 tablas
- Portal warehouse: dashboard, lotes (con anulación con motivo y validación de precio al aprobar), rutas (con CRUD en borrador y paradas multi-ítem), clientes, incidencias, negociación de precios multi-transportista con sugerencia automática y aprobación de holding (con pantalla propia "Ofertas por ruta" para verlas todas juntas), tarifas de servicio administrables, escaneo de inventario, recepción de mercancía (inbound)
- Portal transportista: marketplace de negociación de precios (antes no tenía ninguna UI de negociación) — incluye un widget en el Dashboard para pujar sin salir de ahí; Dashboard con cards + gráficos y matriz de rutas por estado, alineado con el del warehouse (ver [4.7](#47-negociación-de-precios-route_price_offers))
- App móvil: pantallas base del portal warehouse
- **Datos de demo regenerados** (septiembre 2026): `reset_operational_data.py` + `seed_demo_data.py` reescrito reemplazan el dataset viejo (que había derivado a 349 rutas/38 lotes por pruebas manuales acumuladas, muy lejos de lo documentado) por uno limpio de 8 lotes / 16 rutas con paradas reales de Orlando FL, coherente con el estándar de precio/negociación vigente — reusa las funciones reales (`calculate_suggested_price`, `_apply_formula_price`, `_open_route_marketplace`, `_close_negotiation`) en vez de hardcodear precios o estados, y cubre a propósito cada estado relevante para pruebas: borrador, aprobado sin pujas, aprobado con pujas de 2 transportistas en paralelo (se sumó una segunda empresa de transporte, Sunshine Logistics FL, sin verificar, además de Rapid Courier Orlando LLC), en holding, negociada, despacho manual en curso, completado, y cancelado con motivo. Se detectó de paso un bug real en el script viejo: el `append()` de cada ruta al lote estaba mal indentado (fuera del `for`), así que en la práctica solo creaba 1 ruta por lote en vez de 3 — quedó corregido en la reescritura. `seed_demo_data.py` termina imprimiendo el número de lote/ruta de cada escenario para poder referenciarlos directo al probar
- **Bootstrap reproducible en un equipo nuevo**: `seed_initial_data.py` crea de forma idempotente el holding/company/warehouse/usuario admin que antes se armaban a mano y no quedaban en ningún script — verificado de punta a punta (migraciones + bootstrap + seed demo + login) contra una base Postgres vacía. Documentado en una guía de instalación paso a paso (incluye setup de Visual Studio Code vía `.vscode/`)
- **Modelo de seguridad conectado**: el catálogo de perfiles y permisos (ver [4.1](#41-autenticación-y-multi-tenancy)) pasó de ser un catálogo sin uso a estar activamente protegiendo 24 endpoints de escritura, con comportamiento fail-open verificado (al conectarlo eran 14 módulos y 6 perfiles; hoy son 16 módulos y 10 perfiles, con los de transporte)
- **Auditoría automática de cambios, con pantalla en el portal** (ver [4.10](#410-auditoría-audit_logs--septiembre-2026)): cualquier insert/update/delete que pase por el ORM queda registrado con quién, cuándo y qué cambió, sin haber tocado los endpoints existentes; verificado con datos reales del sistema (incluye el caso "sistema" para cambios sin usuario autenticado, ej. un script de seed)
- **Admin de Muevo y portal de administración** (octubre 2026, ver [4.1](#41-autenticación-y-multi-tenancy)): `users.role = muevo_admin` es el personal administrativo de la plataforma; entra a `admin.html` (versión mínima: Compañías con alta, Perfiles de solo lectura, Auditoría con filtro) y ve todos los holdings. Los endpoints de plataforma pasaron a exigir admin de Muevo o super-admin de holding (antes cualquier usuario autenticado podía crear y editar perfiles)
- **Perfiles RBAC de transporte** (octubre 2026, ver [4.1](#41-autenticación-y-multi-tenancy)): `transport_admin`, `transport_dispatcher`, `transport_billing`, `transport_viewer`, asignados por `holding_user_profiles`; 6 endpoints de transporte protegidos con `require_transport_permission()`
- **Conductor por ruta** (octubre 2026, ver [4.1.1](#411-conductor-por-ruta-no-por-vehículo-octubre-2026-migración-031_driver_per_route)): `vehicles.driver_id` eliminado; el conductor se asigna a cada ruta y pertenece a una empresa de transporte. El modal de aceptar ruta elige vehículo y conductor
- **Documentación técnica actualizada** (octubre 2026): diccionario de datos regenerado desde el esquema real (42 tablas, 11 dominios) y DFD multinivel (contexto, 11 procesos, detalle de acceso, planificación, negociación, ejecución, avisos y auditoría, más matriz proceso × almacén) publicado como artifact
- **Usuarios de prueba** (contraseña de todos: `muevo123`): warehouse — `carlos@` y `super.admin@` (Super Admin), `company.admin@`, `warehouse.admin@`, `operator@`, `viewer@`, `billing.manager@`; transporte — `transport@` (Rapid Courier Orlando, verificada) y `sunshine@` (Sunshine Logistics, sin verificar) como Admin de Transporte, más `transport.dispatcher@`, `transport.billing@` y `transport.viewer@` en Rapid Courier; Muevo — `admin@muevo.app`; todos con dominio `@muevo.app`. `driver@muevo.app` es solo de la app móvil
- **Geografía país > estado > ciudad** (octubre 2026, ver [4.1.2](#412-geografía-país--estado--ciudad-octubre-2026-migración-032_geography)): EE.UU., 50 estados y 29.537 ciudades; las rutas se pueden armar por ciudad, estado o país
- **Historial de cambios de perfil** (octubre 2026, ver [4.12](#412-historial-de-cambios-de-perfil-profile_change_history--octubre-2026)): tabla de solo lectura con persona, fecha, cambio, valor anterior y valor actual para perfiles, permisos, asignaciones de perfil, usuarios del holding y accesos a compañías; consulta por API y pestaña en `admin.html`; de paso se cerró el hueco de los DELETE masivos que no quedaban en la auditoría
- **Registro de empresas de transporte con revisión de Muevo** (octubre 2026, ver [4.13](#413-registro-de-empresas-de-transporte-transport_applications--octubre-2026)) y cierre de un hueco de seguridad en `/auth/register` (permitía registrarse como `muevo_admin`)
- **Notificaciones in-app + email en ambos portales** (ver [4.11](#411-notificaciones-notifications--septiembre-2026)): todo el ciclo de negociación/despacho (marketplace se abre, ofertas, holding, aceptar/rechazar rutas) avisa a los usuarios correspondientes — campanita con polling, sin necesidad de refrescar la pantalla a mano

### Pendiente / próximos pasos
- Registro de warehouses (compañías) y de conductores por invitación: falta, con el mismo esquema que el de transporte (ver [4.13](#413-registro-de-empresas-de-transporte-transport_applications--octubre-2026))
- `audit_logs` tiene 4 filas anteriores con hashes de contraseña en `changes`: decidir si se enmascaran (la auditoría es un registro inmutable, no se tocó)
- Geografía: `warehouse.html` no expone todavía el agrupamiento por zona ni los selectores de país/estado/ciudad; `companies` y `transport_companies` siguen con ciudad y estado en texto libre
- Portal admin: la sección "Tarifas" (dentro de `warehouse.html`) ya cubre el catálogo de tipos de servicio; falta gestión de tipos de vehículo y el resto de catálogos restantes
- `admin.html` es una versión mínima: el backend ya soporta editar perfiles, crear y editar warehouses y asignar perfiles a usuarios (`/admin/users/profiles`), pero la pantalla todavía no los expone
- Un conductor puede estar asignado a varias rutas a la vez: no hay validación de solapamiento de horarios (el modal solo deshabilita a quien ya está en una ruta en curso)
- Los usuarios de transporte tienen un `HoldingUser` (sin acceso a ninguna company) solo para colgar su perfil; aparecen en el listado de usuarios del holding. Si molesta, la alternativa es una tabla de perfiles a nivel `User`
- Deploy a producción (actualmente corre local + ngrok para desarrollo)
- Suite de tests automatizados (actualmente validación manual + scripts de fix puntuales)
- Exponer `delivered_at` y `photo_url` (modelo `Delivery`) en los serializers de parada de `main.py` para que la matriz de detalle de ruta muestre hora real de entrega y foto en vez de placeholders (ver [4.3.1](#431-detalle-de-ruta-openroute-en-warehousehtml))
- Decidir si el modal `openShipmentModal` (badges de apilable/frágil/hazmat, instrucciones especiales, desglose por parada) recupera un punto de entrada en la UI o se da de baja — quedó sin trigger tras la última reorganización del detalle de ruta
- El marketplace de negociación es completamente abierto (cualquier transportista activo puede pujar cualquier ruta) — si en algún momento hace falta restringir a una lista de transportistas de confianza por company/holding, hoy no existe ese mecanismo de invitación/whitelist
- Incorporar una métrica de demanda (disponibilidad de transportistas, histórico de aceptación) al cálculo de precio sugerido — hoy solo usa distancia/peso/volumen
- `change_batch_status()` valida el acceso contra la tabla legacy `CompanyAdmin` en vez de `HoldingUserCompany` (el mecanismo moderno) — un usuario con acceso solo vía `HoldingUserCompany` puede quedar bloqueado de aprobar/anular lotes aunque debería poder operar
- Extender `require_permission()` a los endpoints todavía sin conectar: CRUD de rutas/paradas en borrador, y los módulos `companies`/`warehouses`/`users`/`billing`/`reports`
- Auditoría e historial de perfiles: el rol de la aplicación es superusuario y `audit_logs` no tiene protección contra borrado (el historial de perfiles sí tiene trigger de solo lectura, pero un superusuario puede desactivarlo) — falta un rol de aplicación sin superusuario ni DELETE/UPDATE sobre ambas tablas
- Auditoría: falta una política de purga/archivado — sin volumen real todavía no hace falta, pero en producción con uso sostenido `audit_logs` puede crecer bastante
- Notificaciones: entrega solo por polling cada 30s (ver [4.11](#411-notificaciones-notifications--septiembre-2026)), no hay push en tiempo real vía WebSocket — decisión deliberada de v1, no una limitación técnica
- Notificaciones: no existen preferencias por usuario ni opt-out — a diferencia de `BatchClient` (que sí tiene toggles granulares de email), cada admin de company/transport-company recibe incondicionalmente todo evento relevante a su alcance

---

## 7. Convenciones del proyecto

- **Scripts `fix_*.py`** — se usan para corregir datos inconsistentes en la base ya poblada (ej: rutas sin tipo de vehículo, lotes sin numerar). Se prefieren sobre migraciones de datos dentro de Alembic porque son más fáciles de auditar y re-ejecutar de forma idempotente durante desarrollo activo.
- **Numeración de migraciones secuencial de 3 dígitos** (`001` a `033` actualmente) con nombre descriptivo del cambio.
- **No usar DELETE/UPDATE masivos en endpoints** (`sa_delete` / `sa_update`) sobre tablas que deban quedar auditadas: no pasan por el listener de SQLAlchemy y el cambio no queda en `audit_logs` ni en el historial de perfiles. Borrar y actualizar objeto por objeto (`db.delete(obj)`, `obj.campo = valor`)
- **Español en UI y comentarios de negocio, inglés en nombres de variables/funciones técnicas** — refleja que el equipo de producto/negocio es hispanohablante pero el código sigue convención internacional.
- **Bootstrap de una base nueva**: `seed_initial_data.py` (holding → company → warehouse → usuario admin) va primero y es idempotente; recién después corren `seed_demo_data.py` y `seed_incident_types.py`, que asumen que ya existe al menos una `Company`. Los catálogos base (`service_types`, `vehicle_types`) no necesitan seed aparte — ya vienen sembrados por las migraciones `012`/`013`. `seed_security_extensions.py` puede correr en cualquier momento después de `seed_initial_data.py` — agrega módulos/permisos nuevos y le asigna un perfil por defecto a los `HoldingUser` que todavía no tengan uno. `seed_test_profile_users.py` es opcional y solo sirve para pruebas manuales — crea un usuario (`muevo123`) por cada uno de los 6 perfiles de seguridad, todos con acceso a la primera `Company` que encuentre.
- **`.vscode/` versionado** (`launch.json`, `settings.json`, `extensions.json`) — intérprete apuntando al `.venv` del proyecto y una configuración de debug que levanta `uvicorn main:app --reload` con F5, para no depender de que cada desarrollador arme su propio setup.
