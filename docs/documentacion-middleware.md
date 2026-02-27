# API-Odoo Middleware - Documentacion General

## Informacion del Documento

| Campo | Detalle |
|-------|---------|
| Proyecto | API-Odoo Middleware |
| Version | 1.0 |
| Fecha | Febrero 2026 |
| Equipo | Smart Automat AI |
| Clasificacion | Uso interno |

---

## Tabla de Contenidos

1. [Introduccion](#1-introduccion)
2. [Arquitectura del Sistema](#2-arquitectura-del-sistema)
3. [Estructura del Proyecto](#3-estructura-del-proyecto)
4. [Configuracion y Despliegue](#4-configuracion-y-despliegue)
5. [API - Endpoints y Uso](#5-api---endpoints-y-uso)
6. [Modelos y Metodos Permitidos](#6-modelos-y-metodos-permitidos)
7. [Seguridad](#7-seguridad)
8. [Ejemplos de Integracion](#8-ejemplos-de-integracion)
9. [Scripts Utilitarios](#9-scripts-utilitarios)
10. [Integraciones Avanzadas](#10-integraciones-avanzadas)
11. [Testing y Calidad](#11-testing-y-calidad)
12. [Manejo de Errores](#12-manejo-de-errores)
13. [Despliegue en Produccion](#13-despliegue-en-produccion)
14. [FAQ - Preguntas Frecuentes](#14-faq---preguntas-frecuentes)
15. [Checklist de Integracion](#15-checklist-de-integracion)

---

## 1. Introduccion

### Que es API-Odoo Middleware?

API-Odoo es un **middleware universal** desarrollado en Python con FastAPI que actua como puente entre el ERP Odoo y cualquier sistema externo (agentes de IA, hojas de calculo, scripts, bases de datos, aplicaciones web).

### Problema que Resuelve

Odoo utiliza el protocolo **JSON-RPC** para comunicaciones, que es complejo de implementar directamente. Este middleware:

- **Simplifica** la integracion exponiendo una API REST estandar (HTTP/JSON)
- **Asegura** el acceso con autenticacion por API Key y listas blancas
- **Centraliza** la conexion a Odoo en un unico punto de acceso
- **Protege** contra abuso con rate limiting y logging de auditoria

### Beneficios Clave

| Beneficio | Descripcion |
|-----------|-------------|
| Simplicidad | Un solo endpoint POST para todas las operaciones |
| Seguridad | API Key + whitelist de modelos y metodos |
| Universalidad | Funciona con cualquier lenguaje que haga HTTP |
| Observabilidad | Logging estructurado de cada peticion |
| Multi-tenant | Soporte para multiples instancias de Odoo |
| Rendimiento | Rate limiting de 60 req/min por IP |

---

## 2. Arquitectura del Sistema

### Diagrama de Flujo

```
+-------------------+         +-------------------+         +-------------------+
|                   |  HTTPS  |                   | JSON-RPC|                   |
|  Agente / Cliente +-------->+  API-Odoo         +-------->+  Odoo ERP         |
|  (Python, JS,     |  POST   |  Middleware        |         |  (Instancia)      |
|   Excel, curl)    |<--------+  (FastAPI)         |<--------+                   |
|                   |  JSON   |                   |  JSON   |                   |
+-------------------+         +-------------------+         +-------------------+
                                      |
                              +-------+-------+
                              |               |
                          Validacion      Logging
                          - API Key       - Timestamp
                          - Whitelist     - Modelo
                          - Rate Limit    - Metodo
                                          - Resultado
```

### Componentes Principales

| Componente | Archivo | Responsabilidad |
|-----------|---------|-----------------|
| API REST | `api.py` | Recibe peticiones HTTP, valida, responde |
| Conector Odoo | `odoo_universal.py` | Traduce HTTP a JSON-RPC, comunica con Odoo |
| Configuracion | `.env` | Credenciales y parametros del entorno |
| Scripts | `scripts/` | Utilidades de sincronizacion y mantenimiento |
| Tests | `tests/` | Suite de pruebas automatizadas |
| Documentacion | `docs/` | Guias de integracion y ejemplos |

---

## 3. Estructura del Proyecto

```
API-Odoo/
|
|-- api.py                      # Aplicacion FastAPI (punto de entrada)
|-- odoo_universal.py           # Clase conector JSON-RPC a Odoo
|-- config.json                 # Ejemplo de configuracion de peticion
|-- requirements.txt            # Dependencias Python
|-- Dockerfile                  # Configuracion de contenedor
|-- .env.example                # Plantilla de variables de entorno
|-- .env                        # Credenciales reales (NO se sube a git)
|-- .gitignore                  # Exclusiones de git
|-- README.md                   # Guia rapida de inicio
|
|-- .github/
|   +-- copilot-instructions.md # Instrucciones para asistentes de IA
|
|-- docs/
|   |-- guia-agentes.md         # Guia completa de integracion
|   |-- ejemplos-agentes.md     # Ejemplos de codigo multi-lenguaje
|   |-- power-query-template.md # Integracion con Excel Power Query
|   +-- etl-sync.md             # Sincronizacion ETL a bases de datos
|
|-- scripts/
|   |-- borrar_duplicados.py    # Eliminar contactos duplicados
|   |-- crear_clientes_prueba.py# Crear clientes de prueba
|   |-- excel_a_odoo.py         # Sincronizar Excel a Odoo
|   +-- generar_excel_demo.py   # Generar Excel de demostracion
|
+-- tests/
    |-- __init__.py
    +-- test_api.py             # Suite de pruebas unitarias e integracion
```

### Descripcion de Archivos Principales

#### `api.py` - Aplicacion FastAPI
- Define los endpoints REST (`/health`, `/odoo`, `/docs`)
- Implementa autenticacion por API Key via header `X-Api-Key`
- Aplica whitelist de modelos y metodos permitidos
- Configura rate limiting (60 req/min por IP)
- Logging estructurado con timestamps ISO

#### `odoo_universal.py` - Conector Odoo
- Clase `OdooUniversalAPI` que gestiona la comunicacion JSON-RPC
- Autenticacion automatica con Odoo
- Ejecucion de metodos en modelos de Odoo
- Manejo de timeouts (30 segundos por defecto)
- Excepciones personalizadas: `OdooConnectionError`, `OdooExecutionError`
- Soporte multi-tenant con `register_tenant()` y `get_tenant()`

---

## 4. Configuracion y Despliegue

### Variables de Entorno

Crear archivo `.env` basado en `.env.example`:

```env
# Conexion a Odoo
ODOO_URL=https://tu-instancia.odoo.com
ODOO_DB=nombre-base-datos
ODOO_USERNAME=usuario@empresa.com
ODOO_PASSWORD=contraseña-segura

# Seguridad del middleware
API_KEY=clave-generada-con-secrets

# Modelos permitidos (separados por coma)
ALLOWED_MODELS=account.move,res.partner,sale.order,purchase.order,stock.picking,product.template

# Metodos permitidos (separados por coma)
ALLOWED_METHODS=search_read,read,fields_get,name_search,create,write,unlink
```

### Generar API Key Segura

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Esto genera un token hexadecimal de 256 bits criptograficamente seguro.

### Instalacion Local

```bash
# 1. Crear entorno virtual
python -m venv venv

# 2. Activar entorno
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con los valores reales

# 5. Ejecutar servidor
uvicorn api:app --reload
```

El servidor estara disponible en `http://localhost:8000`.

### Despliegue con Docker

```bash
# Construir imagen
docker build -t api-odoo .

# Ejecutar contenedor
docker run --env-file .env -p 8000:8000 api-odoo
```

### Dependencias del Proyecto

| Paquete | Proposito |
|---------|-----------|
| `fastapi` | Framework web de alto rendimiento |
| `uvicorn[standard]` | Servidor ASGI para FastAPI |
| `requests` | Cliente HTTP para comunicacion con Odoo |
| `pydantic` | Validacion de datos y modelos |
| `python-dotenv` | Carga de variables de entorno desde `.env` |
| `slowapi` | Rate limiting por IP |
| `httpx` | Cliente HTTP asincrono |
| `pytest` | Framework de pruebas |
| `pytest-asyncio` | Soporte para pruebas asincronas |

---

## 5. API - Endpoints y Uso

### Endpoints Disponibles

| Metodo | Ruta | Autenticacion | Descripcion |
|--------|------|--------------|-------------|
| GET | `/health` | No | Estado del servicio |
| POST | `/odoo` | Si (API Key) | Operacion universal sobre Odoo |
| GET | `/docs` | No | Documentacion interactiva Swagger |

### GET /health

Verifica que el servicio esta activo y conectado a Odoo.

**Request:**
```bash
curl https://tu-servidor:8000/health
```

**Response (200):**
```json
{
  "status": "ok"
}
```

### POST /odoo

Endpoint universal para todas las operaciones con Odoo.

**Headers requeridos:**
```
Content-Type: application/json
X-Api-Key: tu-api-key
```

**Cuerpo de la peticion (OdooRequest):**

| Campo | Tipo | Requerido | Descripcion |
|-------|------|-----------|-------------|
| `model` | string | Si | Modelo de Odoo (ej: `res.partner`) |
| `method` | string | Si | Metodo a ejecutar (ej: `search_read`) |
| `args` | list | No | Argumentos posicionales (default: `[]`) |
| `kwargs` | dict | No | Argumentos con nombre (default: `{}`) |
| `tenant` | string | No | Instancia multi-tenant (default: `"default"`) |

**Request de ejemplo:**
```bash
curl -X POST https://tu-servidor:8000/odoo \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: tu-api-key" \
  -d '{
    "model": "res.partner",
    "method": "search_read",
    "args": [[["customer_rank", ">", 0]]],
    "kwargs": {"fields": ["id", "name", "email"], "limit": 10}
  }'
```

**Response exitosa (200):**
```json
{
  "result": [
    {"id": 1, "name": "Empresa ABC", "email": "contacto@abc.com"},
    {"id": 2, "name": "Cliente XYZ", "email": "info@xyz.com"}
  ]
}
```

### GET /docs

Accede a la documentacion interactiva generada por Swagger UI. Abre en el navegador:

```
https://tu-servidor:8000/docs
```

---

## 6. Modelos y Metodos Permitidos

### Modelos de Odoo Disponibles

| Modelo | Descripcion | Campos Comunes |
|--------|-------------|----------------|
| `res.partner` | Contactos y clientes | id, name, email, phone, vat, customer_rank |
| `account.move` | Facturas y asientos contables | name, amount_total, state, move_type, partner_id |
| `sale.order` | Pedidos de venta | name, state, amount_total, partner_id, date_order |
| `purchase.order` | Pedidos de compra | name, state, amount_total, partner_id |
| `stock.picking` | Movimientos de almacen | name, state, origin, partner_id, scheduled_date |
| `product.template` | Catalogo de productos | name, list_price, qty_available, type |

### Metodos Permitidos

| Metodo | Tipo | Descripcion |
|--------|------|-------------|
| `search_read` | Lectura | Buscar y leer registros con filtros |
| `read` | Lectura | Leer registros por IDs especificos |
| `fields_get` | Lectura | Obtener definicion de campos de un modelo |
| `name_search` | Lectura | Buscar registros por nombre (autocompletado) |
| `create` | Escritura | Crear un nuevo registro |
| `write` | Escritura | Actualizar registros existentes |
| `unlink` | Escritura | Eliminar registros |

### Sintaxis de Filtros (Dominios de Odoo)

Los filtros usan la sintaxis de dominios de Odoo: `[["campo", "operador", "valor"]]`

| Operador | Descripcion | Ejemplo |
|----------|-------------|---------|
| `=` | Igual | `["state", "=", "posted"]` |
| `!=` | Diferente | `["state", "!=", "cancel"]` |
| `>` | Mayor que | `["amount_total", ">", 1000]` |
| `<` | Menor que | `["amount_total", "<", 500]` |
| `>=` | Mayor o igual | `["customer_rank", ">=", 1]` |
| `<=` | Menor o igual | `["qty_available", "<=", 10]` |
| `like` | Contiene (case sensitive) | `["name", "like", "Tech"]` |
| `ilike` | Contiene (case insensitive) | `["name", "ilike", "tech"]` |
| `in` | Dentro de lista | `["state", "in", ["draft", "posted"]]` |
| `not in` | Fuera de lista | `["state", "not in", ["cancel"]]` |

---

## 7. Seguridad

### Capas de Seguridad

```
Peticion HTTP
    |
    v
[1. Rate Limiting] --> 429 si excede 60 req/min
    |
    v
[2. API Key]       --> 401 si falta o es incorrecta
    |
    v
[3. Whitelist Modelo] --> 422 si modelo no permitido
    |
    v
[4. Whitelist Metodo] --> 422 si metodo no permitido
    |
    v
[5. Logging]       --> Registra cada operacion
    |
    v
[6. Odoo]          --> Ejecuta la operacion
```

### Detalle de Mecanismos

| Mecanismo | Descripcion | Configuracion |
|-----------|-------------|---------------|
| API Key | Token en header `X-Api-Key` | Variable `API_KEY` en `.env` |
| Whitelist de modelos | Solo modelos autorizados | Variable `ALLOWED_MODELS` en `.env` |
| Whitelist de metodos | Solo metodos autorizados | Variable `ALLOWED_METHODS` en `.env` |
| Rate Limiting | 60 peticiones/minuto por IP | Configurado en `api.py` via slowapi |
| Logging | Registro de cada peticion | Archivo de logs con formato ISO |
| Timeout | 30 segundos maximo por peticion a Odoo | Configurado en `odoo_universal.py` |

### Buenas Practicas de Seguridad

1. **Nunca** subir el archivo `.env` al repositorio
2. **Siempre** usar HTTPS en produccion
3. **Rotar** la API Key periodicamente
4. **Limitar** los modelos y metodos al minimo necesario
5. **Monitorear** los logs de auditoria regularmente
6. **Usar** API Keys diferentes para cada sistema que se integre

---

## 8. Ejemplos de Integracion

### Python - Consultar Clientes

```python
import requests

API_URL = "https://tu-servidor:8000/odoo"
API_KEY = "tu-api-key"

response = requests.post(API_URL, json={
    "model": "res.partner",
    "method": "search_read",
    "args": [[["customer_rank", ">", 0]]],
    "kwargs": {"fields": ["id", "name", "email", "phone"], "limit": 50}
}, headers={"X-Api-Key": API_KEY})

clientes = response.json()["result"]
for cliente in clientes:
    print(f"{cliente['name']} - {cliente['email']}")
```

### Python - Crear Contacto

```python
response = requests.post(API_URL, json={
    "model": "res.partner",
    "method": "create",
    "args": [{
        "name": "Nueva Empresa SL",
        "email": "contacto@nuevaempresa.com",
        "phone": "+34 910 000 001",
        "vat": "B12345678",
        "customer_rank": 1
    }]
}, headers={"X-Api-Key": API_KEY})

nuevo_id = response.json()["result"]
print(f"Contacto creado con ID: {nuevo_id}")
```

### Python - Actualizar Contacto

```python
response = requests.post(API_URL, json={
    "model": "res.partner",
    "method": "write",
    "args": [[nuevo_id], {"email": "nuevo@correo.com"}]
}, headers={"X-Api-Key": API_KEY})

print("Actualizado:", response.json()["result"])  # True
```

### Python - Eliminar Contacto

```python
response = requests.post(API_URL, json={
    "model": "res.partner",
    "method": "unlink",
    "args": [[nuevo_id]]
}, headers={"X-Api-Key": API_KEY})

print("Eliminado:", response.json()["result"])  # True
```

### Python - Consultar Facturas

```python
response = requests.post(API_URL, json={
    "model": "account.move",
    "method": "search_read",
    "args": [[["move_type", "=", "out_invoice"]]],
    "kwargs": {
        "fields": ["name", "partner_id", "amount_total", "state"],
        "limit": 20,
        "order": "create_date desc"
    }
}, headers={"X-Api-Key": API_KEY})

facturas = response.json()["result"]
for f in facturas:
    print(f"{f['name']} | {f['partner_id'][1]} | ${f['amount_total']} | {f['state']}")
```

### Python - Consultar Campos Disponibles

```python
response = requests.post(API_URL, json={
    "model": "res.partner",
    "method": "fields_get",
    "args": [],
    "kwargs": {"attributes": ["string", "type", "required"]}
}, headers={"X-Api-Key": API_KEY})

campos = response.json()["result"]
for nombre, info in campos.items():
    print(f"{nombre}: {info['string']} ({info['type']})")
```

### Node.js / JavaScript

```javascript
const response = await fetch("https://tu-servidor:8000/odoo", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Api-Key": "tu-api-key"
  },
  body: JSON.stringify({
    model: "sale.order",
    method: "search_read",
    args: [[["state", "=", "sale"]]],
    kwargs: { fields: ["name", "amount_total", "partner_id"], limit: 10 }
  })
});

const data = await response.json();
console.log(data.result);
```

### curl

```bash
# Consultar clientes
curl -X POST https://tu-servidor:8000/odoo \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: tu-api-key" \
  -d '{"model":"res.partner","method":"search_read","args":[[["customer_rank",">",0]]],"kwargs":{"fields":["name","email"],"limit":5}}'

# Verificar salud del servicio
curl https://tu-servidor:8000/health
```

### Cliente Python Reutilizable

```python
import requests

class OdooClient:
    def __init__(self, url, api_key):
        self.url = url
        self.headers = {
            "Content-Type": "application/json",
            "X-Api-Key": api_key
        }

    def _call(self, model, method, args=None, kwargs=None):
        payload = {
            "model": model,
            "method": method,
            "args": args or [],
            "kwargs": kwargs or {}
        }
        resp = requests.post(self.url, json=payload, headers=self.headers)
        resp.raise_for_status()
        return resp.json()["result"]

    def search_read(self, model, domain=None, fields=None, limit=100):
        return self._call(model, "search_read",
            args=[domain or []],
            kwargs={"fields": fields or [], "limit": limit})

    def create(self, model, values):
        return self._call(model, "create", args=[values])

    def write(self, model, ids, values):
        return self._call(model, "write", args=[ids, values])

    def unlink(self, model, ids):
        return self._call(model, "unlink", args=[ids])

    def fields_get(self, model, attributes=None):
        return self._call(model, "fields_get",
            kwargs={"attributes": attributes or ["string", "type"]})

# Uso:
client = OdooClient("https://tu-servidor:8000/odoo", "tu-api-key")
clientes = client.search_read("res.partner",
    domain=[["customer_rank", ">", 0]],
    fields=["name", "email"],
    limit=10)
```

---

## 9. Scripts Utilitarios

### `scripts/crear_clientes_prueba.py`

**Proposito:** Crear un archivo Excel con clientes de prueba y subirlos a Odoo.

**Caracteristicas:**
- Genera un archivo `.xlsx` con 11 clientes de prueba estilizado
- Verifica que el cliente no exista antes de crearlo (evita duplicados)
- Muestra estadisticas de creacion

**Uso:**
```bash
python scripts/crear_clientes_prueba.py
```

### `scripts/borrar_duplicados.py`

**Proposito:** Buscar y eliminar contactos duplicados basados en NIF/VAT.

**Caracteristicas:**
- Agrupa registros por NIF
- Conserva el mas antiguo (menor ID), elimina los demas
- Modo `--dry-run` para previsualizar sin eliminar
- Confirmacion interactiva antes de eliminar

**Uso:**
```bash
# Previsualizar duplicados sin eliminar
python scripts/borrar_duplicados.py --dry-run

# Eliminar duplicados (con confirmacion)
python scripts/borrar_duplicados.py
```

### `scripts/excel_a_odoo.py`

**Proposito:** Sincronizacion bidireccional entre Excel y Odoo.

**Caracteristicas:**
- Hoja "Contactos" con ID: actualiza registros existentes (`write`)
- Hoja "Nuevos_Contactos" sin ID: crea registros nuevos (`create`)
- Autodeteccion de cabeceras y normalizacion de campos
- Reporte detallado por fila

**Uso:**
```bash
python scripts/excel_a_odoo.py [archivo.xlsx]
```

### `scripts/generar_excel_demo.py`

**Proposito:** Generar un Excel de demostracion con datos reales de Odoo.

**Genera 4 hojas:**
1. **Contactos** - Datos actuales de Odoo
2. **Nuevos_Contactos** - Plantilla para agregar nuevos
3. **Instrucciones** - Guia de uso paso a paso
4. **Configuracion** - Parametros del middleware

**Uso:**
```bash
python scripts/generar_excel_demo.py
```

---

## 10. Integraciones Avanzadas

### Excel Power Query

Se puede conectar Excel directamente al middleware usando Power Query. Consultar la guia detallada en `docs/power-query-template.md`.

**Resumen del flujo:**
1. Abrir Excel > Datos > Obtener datos > Consulta en blanco
2. Pegar la formula M proporcionada en la documentacion
3. Configurar URL, API Key, modelo y campos
4. Los datos se actualizan con un clic

### ETL a Base de Datos (PostgreSQL/MySQL)

Se puede sincronizar datos de Odoo a una base de datos relacional. Consultar `docs/etl-sync.md`.

**Flujo ETL:**
```
Odoo --> Middleware API --> Script Python --> PostgreSQL/MySQL
                                  |
                          (Extrae, Transforma, Carga)
```

**Caracteristicas:**
- Paginacion automatica para grandes volumenes
- Upsert (insert o update segun exista)
- Transformacion de campos relacionales
- Automatizacion con cron (Linux/Windows/Docker)

### Multi-Tenant

El middleware soporta multiples instancias de Odoo simultaneamente:

```python
# Registrar tenants
from odoo_universal import OdooUniversalAPI, register_tenant

api_prod = OdooUniversalAPI("https://prod.odoo.com", "db_prod", "user", "pass")
api_test = OdooUniversalAPI("https://test.odoo.com", "db_test", "user", "pass")

register_tenant("produccion", api_prod)
register_tenant("testing", api_test)

# Usar desde la API
{
  "model": "res.partner",
  "method": "search_read",
  "args": [[]],
  "kwargs": {"limit": 5},
  "tenant": "produccion"
}
```

---

## 11. Testing y Calidad

### Ejecutar Tests

```bash
pytest tests/ -v
```

### Cobertura de Tests

La suite incluye **19+ casos de prueba** organizados en 5 categorias:

| Categoria | Tests | Que Valida |
|-----------|-------|-----------|
| Health | 2 | Respuesta sin autenticacion, estado "ok" |
| Autenticacion | 4 | API Key ausente, incorrecta, correcta, modo dev |
| Whitelist | 4 | Modelo no permitido, metodo no permitido, permitidos, sin whitelist |
| Errores | 3 | Error de conexion (503), error de ejecucion (422), tenant inexistente (400) |
| Funcionalidad | 2+ | Respuesta contiene "result", tenant default |

### Estrategia de Mocking

Los tests **no requieren conexion real a Odoo**. Utilizan:
- `monkeypatch` para variables de entorno
- Mocks de `OdooUniversalAPI._login` para evitar autenticacion real
- Mocks de `execute` para retornar datos predecibles

---

## 12. Manejo de Errores

### Codigos de Respuesta HTTP

| Codigo | Significado | Causa | Accion Recomendada |
|--------|-------------|-------|-------------------|
| 200 | Exito | Operacion completada | Procesar `result` |
| 400 | Bad Request | Tenant no configurado | Verificar campo `tenant` |
| 401 | Unauthorized | API Key falta o incorrecta | Verificar header `X-Api-Key` |
| 422 | Unprocessable | Modelo/metodo no permitido o error en Odoo | Revisar whitelist y parametros |
| 429 | Too Many Requests | Excedio 60 req/min | Esperar y reintentar |
| 503 | Service Unavailable | Odoo no accesible | Verificar conexion a Odoo |
| 500 | Internal Error | Error inesperado del middleware | Reportar al equipo |

### Formato de Error

```json
{
  "detail": "Descripcion del error en texto legible"
}
```

### Patron de Reintento Recomendado

```python
import time

def call_with_retry(payload, max_retries=3):
    for attempt in range(max_retries):
        response = requests.post(API_URL, json=payload, headers=headers)

        if response.status_code == 200:
            return response.json()["result"]

        if response.status_code == 429:  # Rate limit
            wait = 2 ** attempt  # Backoff exponencial: 1s, 2s, 4s
            time.sleep(wait)
            continue

        if response.status_code == 503:  # Odoo no disponible
            time.sleep(5)
            continue

        # Otros errores: no reintentar
        response.raise_for_status()

    raise Exception("Maximo de reintentos alcanzado")
```

---

## 13. Despliegue en Produccion

### Requisitos

| Requisito | Detalle |
|-----------|---------|
| HTTPS | Certificado SSL obligatorio |
| Reverse Proxy | Nginx o Caddy recomendado |
| IP Publica | El servidor debe ser accesible desde los clientes |
| Secretos | Gestion segura de variables de entorno |
| API Key | Token de 256 bits (64 caracteres hexadecimales) |
| Monitoreo | Healthcheck periodico a `/health` |

### Ejemplo Nginx

```nginx
server {
    listen 443 ssl;
    server_name api-odoo.tu-empresa.com;

    ssl_certificate /etc/ssl/certs/tu-cert.pem;
    ssl_certificate_key /etc/ssl/private/tu-key.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

### Docker Compose (Ejemplo)

```yaml
version: "3.8"
services:
  api-odoo:
    build: .
    env_file: .env
    ports:
      - "8000:8000"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

---

## 14. FAQ - Preguntas Frecuentes

### Conexion y Autenticacion

**P: Como obtengo mi API Key?**
R: El administrador del middleware la genera con `python -c "import secrets; print(secrets.token_hex(32))"` y la configura en el `.env`.

**P: Puedo usar el middleware sin API Key?**
R: Si, en modo desarrollo. Si `API_KEY` esta vacia en `.env`, no se requiere autenticacion. **Nunca hacer esto en produccion.**

**P: El middleware soporta multiples instancias de Odoo?**
R: Si, mediante el campo `tenant` en cada peticion. Cada tenant se configura con sus propias credenciales.

### Datos y Modelos

**P: Como se que campos tiene un modelo?**
R: Usar el metodo `fields_get`:
```json
{"model": "res.partner", "method": "fields_get", "kwargs": {"attributes": ["string", "type"]}}
```

**P: Puedo acceder a modelos que no estan en la whitelist?**
R: No. Se recibira un error 422. Contactar al administrador para agregar el modelo necesario.

**P: Hay limite de registros por consulta?**
R: Use el parametro `limit` en kwargs. Se recomienda no superar 500 registros por peticion.

### Operaciones de Escritura

**P: Como creo un registro?**
R: Usar metodo `create` con los datos como diccionario en `args`:
```json
{"model": "res.partner", "method": "create", "args": [{"name": "Nuevo", "email": "a@b.com"}]}
```

**P: Como actualizo un registro?**
R: Usar metodo `write` con la lista de IDs y los campos a actualizar:
```json
{"model": "res.partner", "method": "write", "args": [[123], {"email": "nuevo@correo.com"}]}
```

**P: Como elimino un registro?**
R: Usar metodo `unlink` con la lista de IDs:
```json
{"model": "res.partner", "method": "unlink", "args": [[123]]}
```

### Errores Comunes

**P: Recibo error 503 - Service Unavailable**
R: El middleware no puede conectar con Odoo. Verificar que la URL de Odoo es correcta y que el servicio esta activo.

**P: Recibo error 429 - Too Many Requests**
R: Se excedio el limite de 60 peticiones por minuto. Implementar backoff exponencial.

**P: Recibo error 422 con "modelo no permitido"**
R: El modelo no esta en la whitelist. Verificar `ALLOWED_MODELS` en `.env`.

---

## 15. Checklist de Integracion

Antes de poner una integracion en produccion, verificar:

- [ ] URL del middleware es correcta y accesible via HTTPS
- [ ] API Key esta configurada y funciona
- [ ] Endpoint `/health` responde `{"status": "ok"}`
- [ ] Los modelos necesarios estan en la whitelist
- [ ] Los metodos necesarios estan en la whitelist
- [ ] Las consultas de lectura (`search_read`) funcionan correctamente
- [ ] Las operaciones de escritura (`create`, `write`) funcionan si son necesarias
- [ ] Se implemento manejo de errores para todos los codigos HTTP
- [ ] Se implemento patron de reintento con backoff exponencial
- [ ] El rate limiting (60 req/min) es suficiente para el caso de uso
- [ ] Los logs del middleware se monitorean
- [ ] Se tiene un plan de contingencia si Odoo no esta disponible
- [ ] La API Key no esta hardcodeada en el codigo fuente

---

*Documento generado para uso interno de Smart Automat AI. Febrero 2026.*
