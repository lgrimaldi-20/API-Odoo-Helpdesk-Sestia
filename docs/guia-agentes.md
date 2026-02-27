# Guia para Equipos de Agentes — API-Odoo Middleware

> Documento destinado a desarrolladores que integran agentes de IA, bots o
> sistemas automatizados con el middleware API-Odoo.

---

## Tabla de contenidos

1. [Que es el middleware y para que sirve](#1-que-es-el-middleware)
2. [Requisitos de conexion](#2-requisitos-de-conexion)
3. [Estructura de una peticion](#3-estructura-de-una-peticion)
4. [Modelos y metodos disponibles](#4-modelos-y-metodos-disponibles)
5. [Ejemplos por caso de uso](#5-ejemplos-por-caso-de-uso)
6. [Manejo de errores](#6-manejo-de-errores)
7. [Cliente reutilizable en Python](#7-cliente-reutilizable-en-python)
8. [Preguntas frecuentes del equipo](#8-preguntas-frecuentes-del-equipo)
9. [Checklist de integracion](#9-checklist-de-integracion)

---

## 1. Que es el middleware

El middleware **API-Odoo** es una capa intermedia entre cualquier agente externo y
el servidor Odoo ERP de la empresa. Traduce peticiones HTTP/JSON simples al
protocolo JSON-RPC que usa Odoo internamente.

```
Agente / Bot / Script
       |
       | HTTP POST (JSON)
       v
  API-Odoo Middleware          <-- este servicio
  (FastAPI · puerto 8000)
       |
       | JSON-RPC
       v
  Odoo ERP (Odoo.sh)
  jessdaved19-conexionapi.odoo.com
```

**Por que usar el middleware en vez de llamar a Odoo directamente?**

| Sin middleware | Con middleware |
|----------------|----------------|
| Necesitas manejar JSON-RPC manualmente | Una sola llamada HTTP/JSON |
| Cada agente guarda credenciales Odoo | Solo el middleware las conoce |
| Sin whitelist ni rate limiting | Control centralizado de acceso |
| Sin log de auditoria | Cada llamada queda registrada |

---

## 2. Requisitos de conexion

| Parametro | Valor |
|-----------|-------|
| URL base | `http://localhost:8000` (local) |
| Endpoint de operaciones | `POST /odoo` |
| Endpoint de estado | `GET /health` |
| Autenticacion | Header `X-Api-Key: <clave>` |
| Content-Type | `application/json` |

### Obtener la API Key

La API Key se encuentra en el archivo `.env` del proyecto (campo `API_KEY`).
Solicita la clave al administrador del middleware. Nunca la incluyas en codigo
que se suba a un repositorio publico.

### Verificar que el servicio esta activo

```bash
curl -s http://localhost:8000/health
```

Respuesta esperada cuando todo esta bien:

```json
{
  "status": "ok",
  "odoo_conectado": true,
  "modelos_permitidos": ["account.move", "product.template", "purchase.order",
                         "res.partner", "sale.order", "stock.picking"],
  "metodos_permitidos": ["create", "fields_get", "name_search", "read",
                         "search_read", "unlink", "write"]
}
```

---

## 3. Estructura de una peticion

### Request (POST /odoo)

```json
{
  "model":  "nombre.del.modelo.odoo",
  "method": "nombre_del_metodo",
  "args":   [],
  "kwargs": {},
  "tenant": "default"
}
```

| Campo | Tipo | Obligatorio | Descripcion |
|-------|------|-------------|-------------|
| `model` | string | Si | Modelo Odoo (ej. `res.partner`) |
| `method` | string | Si | Metodo a ejecutar (ej. `search_read`) |
| `args` | array | No | Argumentos posicionales del metodo |
| `kwargs` | object | No | Argumentos con nombre del metodo |
| `tenant` | string | No | Conexion Odoo a usar (default: `"default"`) |

### Response exitosa

```json
{
  "result": <valor devuelto por Odoo>
}
```

El campo `result` puede ser: una lista de registros, un ID entero, `true`, etc.
Depende del metodo llamado.

### Headers requeridos

```
Content-Type: application/json
X-Api-Key: 7d8c2176cad6ff6b20ecaaddc4fc26115c63ad7e1e1f6c909226cd442bfa84d5
```

---

## 4. Modelos y metodos disponibles

### Modelos permitidos

| Modelo Odoo | Descripcion |
|-------------|-------------|
| `res.partner` | Contactos (clientes, proveedores, empresas) |
| `account.move` | Facturas y asientos contables |
| `sale.order` | Pedidos de venta |
| `purchase.order` | Pedidos de compra |
| `stock.picking` | Movimientos de almacen / envios |
| `product.template` | Catalogo de productos |

### Metodos permitidos

| Metodo | Operacion | Descripcion |
|--------|-----------|-------------|
| `search_read` | Lectura | Busca y devuelve registros con filtros |
| `read` | Lectura | Lee registros por ID especifico |
| `fields_get` | Lectura | Devuelve la estructura de campos del modelo |
| `name_search` | Lectura | Busca registros por nombre (autocompletado) |
| `create` | Escritura | Crea un registro nuevo, devuelve el ID |
| `write` | Escritura | Actualiza registros existentes por ID |
| `unlink` | Escritura | Elimina registros por ID |

> Para agregar modelos o metodos al whitelist, el administrador debe editar
> `ALLOWED_MODELS` / `ALLOWED_METHODS` en el `.env` del middleware y reiniciarlo.

---

## 5. Ejemplos por caso de uso

### Leer contactos (clientes)

```python
payload = {
    "model": "res.partner",
    "method": "search_read",
    "args": [[["customer_rank", ">", 0]]],
    "kwargs": {
        "fields": ["id", "name", "email", "phone", "vat"],
        "limit": 50,
        "order": "name asc"
    }
}
```

### Buscar facturas del ultimo mes

```python
from datetime import date, timedelta

fecha_desde = (date.today() - timedelta(days=30)).isoformat()

payload = {
    "model": "account.move",
    "method": "search_read",
    "args": [[
        ["move_type", "=", "out_invoice"],
        ["state", "=", "posted"],
        ["invoice_date", ">=", fecha_desde]
    ]],
    "kwargs": {
        "fields": ["name", "partner_id", "amount_total", "invoice_date", "payment_state"],
        "limit": 200
    }
}
```

### Consultar un pedido de venta por numero

```python
payload = {
    "model": "sale.order",
    "method": "search_read",
    "args": [[["name", "=", "S00042"]]],
    "kwargs": {
        "fields": ["name", "partner_id", "amount_total", "state", "date_order"]
    }
}
```

### Crear un contacto nuevo

```python
payload = {
    "model": "res.partner",
    "method": "create",
    "args": [{
        "name": "Empresa Nueva SL",
        "email": "contacto@empresa.com",
        "phone": "+34 910 000 001",
        "vat": "B12345678",
        "customer_rank": 1
    }]
}
# result = ID del nuevo registro (entero)
```

### Actualizar un contacto existente

```python
payload = {
    "model": "res.partner",
    "method": "write",
    "args": [
        [1234],                          # lista de IDs a actualizar
        {"email": "nuevo@email.com"}     # campos a cambiar
    ]
}
# result = True si exito
```

### Consultar campos disponibles de un modelo

```python
payload = {
    "model": "sale.order",
    "method": "fields_get",
    "kwargs": {"attributes": ["string", "type", "required"]}
}
# result = dict con nombre_campo: {string, type, required}
```

### Stock disponible de productos

```python
payload = {
    "model": "product.template",
    "method": "search_read",
    "args": [[["active", "=", True]]],
    "kwargs": {
        "fields": ["id", "name", "type", "list_price", "qty_available"],
        "limit": 100
    }
}
```

---

## 6. Manejo de errores

### Tabla de errores HTTP

| Codigo | Causa | Como resolverlo |
|--------|-------|-----------------|
| `401` | API Key ausente o incorrecta | Verificar header `X-Api-Key` |
| `422` | Modelo/metodo no en whitelist, o error logico de Odoo | Revisar `ALLOWED_MODELS`/`ALLOWED_METHODS`; leer el campo `detail` |
| `429` | Mas de 60 peticiones por minuto desde la misma IP | Agregar espera entre peticiones (backoff) |
| `503` | Middleware no puede conectar con Odoo | Verificar `/health`; Odoo puede estar en mantenimiento |

### Patron de retry recomendado para agentes

```python
import time, requests

def llamar_con_retry(payload, api_url, api_key, max_intentos=3):
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    for intento in range(1, max_intentos + 1):
        try:
            r = requests.post(api_url, json=payload, headers=headers, timeout=30)
            if r.status_code == 429:
                espera = 2 ** intento          # backoff exponencial: 2s, 4s, 8s
                time.sleep(espera)
                continue
            r.raise_for_status()
            return r.json()["result"]
        except requests.HTTPError as e:
            if intento == max_intentos:
                raise
            time.sleep(1)
    raise RuntimeError("Maximos intentos alcanzados")
```

### Interpretar el campo `detail` en errores 422

Cuando Odoo rechaza una operacion (campo duplicado, permisos, etc.), el middleware
devuelve HTTP 422 con un cuerpo como:

```json
{
  "detail": "Error de Odoo: The value '...' already exists in field ..."
}
```

El agente debe leer `response.json()["detail"]` para mostrar el mensaje correcto
al usuario o decidir si reintenta con datos distintos.

---

## 7. Cliente reutilizable en Python

Copia esta clase en tu agente. Es todo lo que necesitas para comunicarte con el
middleware:

```python
import requests


class OdooClient:
    """
    Cliente minimo para el middleware API-Odoo.
    Uso:
        client = OdooClient("http://localhost:8000", "tu-api-key")
        contactos = client.search_read("res.partner",
                                       filters=[["customer_rank", ">", 0]],
                                       fields=["name", "email"])
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.url = base_url.rstrip("/") + "/odoo"
        self.headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
        self.timeout = timeout

    def _post(self, model: str, method: str, args=None, kwargs=None) -> any:
        payload = {
            "model": model,
            "method": method,
            "args": args or [],
            "kwargs": kwargs or {},
        }
        r = requests.post(self.url, json=payload, headers=self.headers,
                          timeout=self.timeout)
        r.raise_for_status()
        return r.json()["result"]

    # --- Metodos de conveniencia ---

    def search_read(self, model, filters=None, fields=None, limit=100, offset=0, order=None):
        kw = {"fields": fields or [], "limit": limit, "offset": offset}
        if order:
            kw["order"] = order
        return self._post(model, "search_read", args=[filters or []], kwargs=kw)

    def read(self, model, ids: list, fields=None):
        return self._post(model, "read", args=[ids], kwargs={"fields": fields or []})

    def create(self, model, values: dict) -> int:
        return self._post(model, "create", args=[values])

    def write(self, model, ids: list, values: dict) -> bool:
        return self._post(model, "write", args=[ids, values])

    def unlink(self, model, ids: list) -> bool:
        return self._post(model, "unlink", args=[ids])

    def fields_get(self, model, attributes=None):
        return self._post(model, "fields_get",
                          kwargs={"attributes": attributes or ["string", "type"]})


# --- Ejemplo de uso ---
if __name__ == "__main__":
    client = OdooClient(
        base_url="http://localhost:8000",
        api_key="7d8c2176cad6ff6b20ecaaddc4fc26115c63ad7e1e1f6c909226cd442bfa84d5",
    )

    # Listar clientes
    clientes = client.search_read(
        "res.partner",
        filters=[["customer_rank", ">", 0]],
        fields=["name", "email", "phone"],
        limit=10,
    )
    for c in clientes:
        print(c["name"], "-", c.get("email", "sin email"))
```

---

## 8. Preguntas frecuentes del equipo

### Conexion y autenticacion

**P: Donde encuentro la API Key para mi agente?**
> En el archivo `.env` del proyecto (campo `API_KEY`). Solicita la clave al
> administrador. Si el middleware esta en produccion, genera una clave fuerte
> con `python -c "import secrets; print(secrets.token_hex(32))"`.

**P: Puedo conectarme al middleware desde fuera de la red local?**
> Actualmente el middleware corre en `localhost:8000` y solo es accesible desde
> la misma maquina. Para exponerlo externamente necesitas: un servidor con IP
> publica, HTTPS (certificado SSL) y un proxy inverso (nginx/caddy).

**P: El header X-Api-Key es case-sensitive?**
> Si. Debe ser exactamente `X-Api-Key`. No funcionara `x-api-key` ni
> `X-API-KEY` en algunos clientes estrictos (aunque FastAPI lo normaliza).

**P: Hay alguna forma de probar sin credenciales reales?**
> Si: abre `http://localhost:8000/docs` en el navegador. Es el Swagger UI
> integrado de FastAPI. Haz clic en "Authorize", pega la API Key, y prueba
> todas las operaciones de forma interactiva sin escribir codigo.

---

### Modelos y datos

**P: Como saber que campos tiene un modelo?**
> Llama a `fields_get` sobre el modelo:
> ```json
> {"model": "sale.order", "method": "fields_get",
>  "kwargs": {"attributes": ["string", "type", "required"]}}
> ```
> Devuelve un diccionario con cada campo y sus metadatos.

**P: Como filtrar registros por fecha?**
> Odoo acepta fechas como strings en formato `YYYY-MM-DD`:
> ```json
> "args": [[["invoice_date", ">=", "2025-01-01"], ["invoice_date", "<=", "2025-12-31"]]]
> ```

**P: Puedo usar OR en los filtros?**
> Si, usando el operador `|` de Odoo:
> ```json
> "args": [["|", ["state", "=", "draft"], ["state", "=", "posted"]]]
> ```

**P: Como obtengo registros de una relacion Many2one? (ej. cliente de una factura)**
> `search_read` devuelve los campos Many2one como `[id, nombre]`:
> ```json
> {"partner_id": [42, "Empresa XYZ SA"]}
> ```
> Para obtener mas datos del partner, haz una segunda llamada con `read` usando ese ID.

**P: Hay un limite de registros por peticion?**
> El middleware no impone limite propio, pero se recomienda `limit: 200` por
> peticion para evitar timeouts. Para datasets grandes, usa paginacion con
> el parametro `offset`.

**P: Como paginar resultados grandes?**
> ```python
> offset = 0
> todos = []
> while True:
>     lote = client.search_read("account.move", limit=200, offset=offset)
>     if not lote:
>         break
>     todos.extend(lote)
>     offset += 200
> ```

---

### Operaciones de escritura

**P: Al crear un registro, que devuelve el middleware?**
> El ID del nuevo registro como entero. Por ejemplo: `{"result": 1523}`.

**P: Puedo actualizar varios registros a la vez?**
> Si. El metodo `write` acepta una lista de IDs:
> ```json
> {"model": "res.partner", "method": "write",
>  "args": [[101, 102, 103], {"active": false}]}
> ```

**P: Como eliminar un registro?**
> Con el metodo `unlink`:
> ```json
> {"model": "res.partner", "method": "unlink", "args": [[1523]]}
> ```
> Devuelve `true` si se elimino correctamente.
> **Advertencia:** la eliminacion en Odoo es permanente. Algunos registros
> con dependencias no se pueden borrar (Odoo lanzara un error 422).

**P: Que pasa si intento escribir en un campo que no existe?**
> Odoo devolvera un error y el middleware lo propagara como HTTP 422 con
> el mensaje de Odoo en el campo `detail`.

---

### Errores comunes

**P: Recibo 422 con "Modelo X no permitido". Como lo soluciono?**
> El modelo no esta en el whitelist `ALLOWED_MODELS` del `.env`. Pide al
> administrador que lo agregue y reinicie el middleware.

**P: Recibo 503. El middleware fallo?**
> No necesariamente. El middleware sigue activo pero no puede alcanzar Odoo
> en ese momento. Verifica `/health` y revisa si Odoo.sh esta en mantenimiento.

**P: El agente hace muchas consultas en poco tiempo y recibe 429.**
> Hay un limite de 60 peticiones por minuto por IP. Soluciones:
> - Agregar `time.sleep(0.1)` entre llamadas consecutivas
> - Usar el patron de retry con backoff exponencial (ver seccion 6)
> - Agrupar consultas (un solo `search_read` grande en lugar de muchos `read`)

**P: La respuesta tarda mucho. Cuanto es el timeout del middleware?**
> El middleware tiene 30 segundos de timeout hacia Odoo. Si Odoo no responde
> en ese tiempo, el middleware devuelve 503. Si tu agente necesita mas tiempo
> para procesar la respuesta, ajusta el timeout en tu cliente HTTP, no en el
> middleware.

---

### Arquitectura y buenas practicas

**P: Debo guardar la API Key en el codigo del agente?**
> No. Guardala en una variable de entorno o en un gestor de secretos
> (AWS Secrets Manager, Azure Key Vault, archivo `.env` local no commiteado).

**P: Puedo llamar al middleware desde multiples agentes en paralelo?**
> Si, con el limite de 60 req/min por IP. Si todos los agentes corren en la
> misma maquina comparten el mismo limite. Para escenarios de alta concurrencia,
> coordina las peticiones o distribuye los agentes en diferentes IPs.

**P: El middleware es stateless?**
> Si. Cada peticion es independiente. El middleware no guarda contexto entre
> llamadas. La sesion con Odoo se establece al arrancar el servicio y se
> reutiliza para todas las peticiones.

**P: Puedo conectar el agente a un Odoo diferente (multi-tenant)?**
> Si. El middleware soporta multi-tenant. Un administrador debe registrar el
> nuevo tenant en `api.py` con `register_tenant("nombre", OdooUniversalAPI(...))`.
> Despues el agente indica `"tenant": "nombre"` en cada peticion.

---

## 9. Checklist de integracion

Antes de poner un agente en produccion, verifica:

- [ ] Tengo la API Key del middleware (NO la guardo en el codigo)
- [ ] Verifique `/health` y el campo `odoo_conectado` es `true`
- [ ] Los modelos que necesito estan en `modelos_permitidos`
- [ ] Los metodos que necesito estan en `metodos_permitidos`
- [ ] Mis peticiones incluyen el header `X-Api-Key` y `Content-Type: application/json`
- [ ] Implemento manejo de errores para codigos 401, 422, 429, 503
- [ ] Tengo logica de retry con backoff para errores 429 y 503
- [ ] Uso paginacion (`limit` + `offset`) para consultas que pueden devolver muchos registros
- [ ] Probe las operaciones de escritura primero en un entorno de prueba
- [ ] El agente registra (logs) cada llamada al middleware para auditoria

---

*Documento mantenido por el equipo de integraciones. Para cambios en whitelist,
credenciales o nuevos tenants, contactar al administrador del middleware.*
