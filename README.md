# API-Odoo | Exportación de Helpdesk

Middleware en Python/FastAPI que **exporta los tickets de Odoo Helpdesk** para
migrarlos al módulo Helpdesk de **SESTIA**, según la especificación de migración
(`migracion-tickets-odoo-helpdesk.pdf`).

Ofrece dos capas, ambas de **solo lectura**:

- **Exportación de Helpdesk** (`/helpdesk/export/*`): genera los tres archivos de
  migración (tickets, historial, adjuntos) más los catálogos previos y los
  conteos de volumen.
- **Proxy JSON-RPC genérico** (`/odoo`): ejecuta consultas sobre cualquier modelo
  de Odoo. Sirve para inspeccionar la instancia del cliente durante la migración
  (qué campos existen, conteos, validar catálogos) sin escribir código.

> **Este middleware no escribe nada en Odoo.** La capa contable del middleware
> original (facturas, pagos, conciliación, inventario) se retiró de este repo:
> no forma parte de la especificación de migración y añadía superficie de
> escritura sobre el Odoo de producción del cliente. Sigue disponible en el
> repositorio original **API-Odoo**.

## Inicio rapido

```bash
# 1. Clonar y crear entorno virtual
python -m venv venv
.\venv\Scripts\Activate     # Windows
# source venv/bin/activate  # Linux/Mac

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
copy .env.example .env      # Windows
# cp .env.example .env      # Linux/Mac
# Edita .env con tus credenciales reales

# 4. Arrancar
uvicorn api:app --reload
```

La API queda disponible en `http://localhost:8000`.
Documentacion interactiva (Swagger): `http://localhost:8000/docs`

## Uso basico

```bash
curl -X POST http://localhost:8000/odoo \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: tu-clave" \
  -d '{"model": "res.partner", "method": "search_read",
       "args": [[["customer_rank", ">", 0]]],
       "kwargs": {"fields": ["name", "email"], "limit": 5}}'
```

## Exportacion de Helpdesk (migracion Odoo -> SESTIA)

Genera los archivos que necesita la importacion de tickets en el modulo Helpdesk
de SESTIA, segun la especificacion de migracion (`migracion-tickets-odoo-helpdesk.pdf`).

Todos los endpoints son **GET de solo lectura**: leen Odoo y devuelven un archivo.
El middleware **no escribe nada** en Odoo en esta capa.

| Archivo | Endpoint | Contenido |
|---------|----------|-----------|
| — | `/helpdesk/export/volumenes` | Conteos previos (JSON). **Empezar por aqui** |
| — | `/helpdesk/export/validar` | Comprueba los tickets contra los catalogos (JSON) |
| `tickets.csv` | `/helpdesk/export/tickets.csv` | Un ticket por fila (UTF-8, RFC 4180) |
| `historial.jsonl` | `/helpdesk/export/historial.jsonl` | Un mensaje del chatter por linea |
| `adjuntos.zip` | `/helpdesk/export/adjuntos.zip` | Binarios + `manifiesto_adjuntos.csv` |
| catalogos | `/helpdesk/export/catalogos.zip` | Un CSV por catalogo (o `/catalogos` en JSON) |

## Flujo completo de una migracion

De cero a los archivos listos para importar. Los pasos siguen el plan de la
seccion 1 de la especificacion.

### Paso 0 — Configurar y comprobar la conexion

Copia `.env.example` a `.env` y rellena los datos de Odoo. Dos valores dan
problemas a menudo:

- **`ODOO_DB`** no siempre es el subdominio. En Odoo.sh se ve con
  `echo $PGDATABASE` en el SHELL; en otras instalaciones, activando el modo
  desarrollador (el nombre aparece al pie del menu de Ajustes).
- **`ODOO_PASSWORD`**: mejor una clave de API (Ajustes -> Usuarios -> tu
  usuario -> Seguridad de la cuenta -> Claves de API) que la contrasena.

```bash
python scripts/verificar_odoo.py
```

Comprueba login, que el modulo Helpdesk este instalado, los permisos de lectura
**modelo a modelo** y los volumenes visibles:

```
[OK]   Login correcto (uid=2)
[OK]   Modulo Helpdesk instalado

Permisos de lectura:
  [OK]   helpdesk.ticket                4 registros  (los tickets)
  [OK]   mail.message                  49 registros  (historial del chatter)
  ...
Volumenes visibles para este usuario:
  Tickets  : 4 (3 abiertos / 1 cerrados)
  Mensajes : 7 (6 comentarios, 1 notas internas, 0 tracking)
  Adjuntos : 2 (0.0 MB)
```

> **Contrasta ese numero de tickets con el que ve un administrador en Odoo.** El
> middleware solo ve lo que su usuario tiene permitido: si las reglas de registro
> le limitan los equipos, la exportacion saldra incompleta **sin dar ningun
> error**, y el conteo estara filtrado igual, asi que no lo detecta solo.

Con la conexion validada, arranca el servidor:

```bash
uvicorn api:app --reload
```

### Paso 1 — Volumenes (antes de exportar nada)

```bash
curl "http://localhost:8000/helpdesk/export/volumenes" -H "X-Api-Key: $API_KEY"
```

```json
{
  "tickets":  {"total": 4, "abiertos": 3, "cerrados": 1},
  "mensajes": {"total": 7, "comentario": 6, "nota_interna": 1, "tracking": 0},
  "adjuntos": {"total": 2, "megabytes": 0.0}
}
```

Con esto se acuerdan las dos decisiones de la seccion 7: **alcance del
historial** (completo o solo para tickets abiertos) y si el **ZIP de adjuntos**
hay que trocearlo.

### Paso 2 — Catalogos, y precargarlos en SESTIA

```bash
curl -o catalogos.zip "http://localhost:8000/helpdesk/export/catalogos.zip" \
  -H "X-Api-Key: $API_KEY"
```

Contiene un CSV por catalogo. Revisa **`etapas.csv`** con atencion: la columna
`es_cierre` es la que decide si un ticket sale como `open` o `closed`.

```csv
equipo,etapa,orden,es_inicial,es_cierre
Soporte,New,0,True,False
Soporte,In Progress,1,False,False
Soporte,Solved,3,False,True
Soporte,Canceled,4,False,True
```

Por defecto se deriva del campo `fold` de Odoo (etapas plegadas en el kanban).
Si no coincide con lo que espera el cliente, se fuerza por nombre sin tocar
codigo — `?etapas_cierre=Solved,Canceled` o `HELPDESK_ETAPAS_CIERRE` en `.env`.

**Antes de seguir**, crea en SESTIA los equipos, fases, categorias y etiquetas
de esos CSV, y verifica que los usuarios existen con el mismo email. La
importacion resuelve por nombre y por email; el catalogo de etiquetas es
**cerrado** (no se crean al vuelo).

### Paso 3 — Muestra de 10-20 tickets

```bash
curl -o muestra.csv "http://localhost:8000/helpdesk/export/tickets.csv?limite=20" \
  -H "X-Api-Key: $API_KEY"
```

Abrela y revisa acentos, fechas y que el `estado` cuadre con la etapa. Es el
paso 4 del plan: validar estructura y encoding antes de la exportacion completa.

### Paso 4 — Validar contra los catalogos

Con los catalogos ya precargados en SESTIA:

```bash
curl "http://localhost:8000/helpdesk/export/validar" -H "X-Api-Key: $API_KEY"
```

```json
{"ok": true, "tickets_revisados": 4, "problemas": {}}
```

Si algo falta, lo dice con ejemplos concretos en vez de fallar fila por fila
durante la importacion:

```json
{
  "ok": false,
  "problemas": {
    "etiquetas": {"vip": ["HT-0001", "HT-0042"]},
    "emails":    {"exagente@empresa.com": ["HT-0007"]},
    "sin_fecha_cierre": ["HT-0015"]
  }
}
```

**No pases de aqui hasta tener `ok: true`.**

### Paso 5 — Exportacion completa

```bash
K="X-Api-Key: $API_KEY"; U=http://localhost:8000/helpdesk/export
curl -o tickets.csv     "$U/tickets.csv"     -H "$K"
curl -o historial.jsonl "$U/historial.jsonl" -H "$K"
curl -o adjuntos.zip    "$U/adjuntos.zip"    -H "$K"
```

Comprobacion rapida de que los tres archivos se enlazan entre si — **todo
adjunto y todo mensaje debe apuntar a un ticket que exista en `tickets.csv`**:

```bash
python - <<'EOF'
import csv, io, json, zipfile
t = {f['odoo_ref'] for f in csv.DictReader(open('tickets.csv', encoding='utf-8-sig'))}
h = {str(json.loads(l)['odoo_ref']) for l in open('historial.jsonl', encoding='utf-8')}
z = zipfile.ZipFile('adjuntos.zip')
m = {f['odoo_ref'] for f in csv.DictReader(
        io.StringIO(z.read('manifiesto_adjuntos.csv').decode()))}
print('tickets:', len(t), '| historial huerfano:', h - t or 'ninguno',
      '| adjuntos huerfanos:', m - t or 'ninguno')
EOF
```

```
tickets: 4 | historial huerfano: ninguno | adjuntos huerfanos: ninguno
```

### Paso 6 — Corte y re-exportacion incremental

Tras la carga inicial, Odoo sigue vivo unos dias. Para traer solo lo que cambio
desde la fecha de corte:

```bash
curl -o tickets-inc.csv \
  "http://localhost:8000/helpdesk/export/tickets.csv?desde=2026-08-01" \
  -H "X-Api-Key: $API_KEY"
```

El filtro es sobre `write_date`, asi que arrastra tambien los tickets
**modificados**, no solo los nuevos. La importacion no duplica: cada ticket
lleva su `odoo_ref`. El mismo `?desde=`/`?hasta=` sirve para **trocear** una
entrega de adjuntos demasiado grande.

### Datos de prueba (solo para QA)

Para probar el circuito completo contra una instancia vacia:

```bash
python scripts/datos_prueba_helpdesk.py --rpc
```

Crea equipo, etapas, etiquetas, contacto y 4 tickets, cada uno pensado para
ejercitar un camino distinto: adjunto real, ticket cerrado con fecha, correo con
hilo citado e imagen embebida, y nota interna frente a comentario publico. Es
idempotente y **es lo unico de este repo que escribe en Odoo** — no lo ejecutes
contra produccion.

### Fecha de corte y re-exportacion incremental

Tras la exportacion completa, los tickets que se sigan creando o modificando en
Odoo se recuperan con una segunda pasada acotada por fecha:

```bash
# Solo lo creado o MODIFICADO desde la fecha de corte.
curl -o tickets-incremental.csv \
  "http://localhost:8000/helpdesk/export/tickets.csv?desde=2026-08-01" \
  -H "X-Api-Key: tu-clave"
```

El filtro es sobre `write_date`, no `create_date`, para que arrastre tambien los
tickets **modificados**. La importacion no duplica: cada ticket lleva su
`odoo_ref` de Odoo. El mismo `?desde=`/`?hasta=` sirve para **trocear** una
entrega de adjuntos demasiado grande.

### Parametros

| Parametro | Endpoints | Efecto |
|-----------|-----------|--------|
| `tenant` | todos | Instancia de Odoo (por defecto `default`) |
| `limite` | los tres archivos | Maximo de tickets (para la muestra) |
| `desde` / `hasta` | los tres archivos y `volumenes` | Ventana ISO 8601 sobre `write_date` |
| `etapas_cierre` | `tickets.csv`, `historial.jsonl` | Etapas de cierre por nombre, separadas por coma |
| `incluir_tracking` | `historial.jsonl` | Incluye las notificaciones automaticas (por defecto **no**) |
| `recortar_citas` | `historial.jsonl` | Recorta el hilo citado y las firmas (por defecto **si**) |
| `solo_abiertos` | `historial.jsonl` | Historial solo de los tickets abiertos |
| `incluir_embebidas` | `adjuntos.zip` | Rescata las imagenes embebidas en los mensajes (por defecto **si**) |

### Decisiones de la exportacion

- **`odoo_ref`**: es la referencia que **enlaza los tres archivos**. Se resuelve
  una sola vez por exportacion (el numero visible del ticket, o su ID si la
  instalacion no lo tiene) y se usa igual en `tickets.csv`, `historial.jsonl` y
  el manifiesto, incluidas las carpetas del ZIP. Si cada archivo usara una
  referencia distinta, la importacion no podria relacionarlos.
- **Estado `open`/`closed`**: se deriva del campo `fold` de la etapa (las etapas
  plegadas en el kanban se toman como de cierre). Se puede forzar con una lista
  explicita de nombres: variable `HELPDESK_ETAPAS_CIERRE` en `.env` o
  `?etapas_cierre=Resuelto,Cancelado` por peticion.
- **Fechas**: ISO 8601 con offset explicito. Odoo las devuelve en UTC sin zona,
  asi que se emiten con sufijo `Z`.
- **Cuerpo de los mensajes**: se entrega en **texto plano** (el HTML de Odoo se
  limpia). Por defecto se recorta el hilo citado y las firmas de los correos.
- **Imagenes embebidas**: las rutas `/web/image/...` mueren al apagar Odoo, asi
  que las imagenes incrustadas en los mensajes se extraen al ZIP de adjuntos y el
  mensaje conserva la referencia para que la importacion las vuelva a enlazar.
- **Campos personalizados**: los campos `x_*` del Odoo del cliente se detectan
  solos y se anaden como columnas extra al final de `tickets.csv`.
- **Usuarios**: se exportan por **email**, no por nombre ni por ID de Odoo. Los
  emails deben coincidir con los de los usuarios en SESTIA.
- **Memoria**: los binarios de los adjuntos se leen de Odoo por lotes, no todos a
  la vez, de modo que una migracion grande no agota la memoria del proceso.

## Seguridad

| Mecanismo | Configuracion |
|-----------|--------------|
| API Key | Variable `API_KEY` en `.env` |
| Whitelist modelos | Variable `ALLOWED_MODELS` (separados por coma) |
| Whitelist metodos | Variable `ALLOWED_METHODS` (separados por coma) |
| Rate limiting | 60 peticiones/minuto por IP |

## Endpoints

| Metodo | Ruta | Auth | Descripcion |
|--------|------|------|-------------|
| GET | `/health` | No | Estado del servicio y conexion Odoo |
| POST | `/odoo` | Si | Consulta generica sobre un modelo de Odoo |
| GET | `/helpdesk/export/volumenes` | Si | Conteos previos a la migracion (JSON) |
| GET | `/helpdesk/export/validar` | Si | Valida los tickets contra los catalogos (JSON) |
| GET | `/helpdesk/export/tickets.csv` | Si | Tickets, un ticket por fila (CSV) |
| GET | `/helpdesk/export/historial.jsonl` | Si | Historial del chatter (JSON Lines) |
| GET | `/helpdesk/export/adjuntos.zip` | Si | Adjuntos + manifiesto (ZIP) |
| GET | `/helpdesk/export/catalogos.zip` | Si | Catalogos, un CSV por catalogo (ZIP) |
| GET | `/helpdesk/export/catalogos` | Si | Catalogos en JSON |
| GET | `/docs` | No | Documentacion interactiva (Swagger) |

## Codigos de respuesta

| Codigo | Significado |
|--------|-------------|
| 200 | Operacion exitosa |
| 401 | API Key invalida o ausente |
| 422 | Modelo/metodo no permitido, error de Odoo o fallo de exportacion |
| 429 | Rate limit superado |
| 503 | Odoo no disponible |

## Docker

```bash
docker build -t api-odoo-helpdesk .
docker run --env-file .env -p 8000:8000 api-odoo-helpdesk

# Equivalente con compose
docker compose up --build
```

## Tests

```bash
pytest tests/ -v
```

## Documentacion adicional

- [docs/guia-agentes.md](docs/guia-agentes.md) - **Guia completa para equipos de agentes** (FAQ, cliente Python, ejemplos)
- [docs/ejemplos-agentes.md](docs/ejemplos-agentes.md) - Ejemplos rapidos en Python, Node.js y curl
- [docs/power-query-template.md](docs/power-query-template.md) - Plantilla M para Excel/Power Query
- [docs/etl-sync.md](docs/etl-sync.md) - Sincronizacion ETL a PostgreSQL/MySQL

La especificacion de la migracion de tickets (campos, formatos y plan de trabajo
acordado con el equipo de SESTIA) esta en `migracion-tickets-odoo-helpdesk.pdf`.
