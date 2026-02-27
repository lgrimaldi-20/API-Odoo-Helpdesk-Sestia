# Instrucciones Copilot para el Middleware API-Odoo

## Resumen
Middleware universal para conectar Odoo ERP con agentes externos (IA, Excel, bases de datos).
Implementado en Python con FastAPI. Expone dos endpoints principales: `/odoo` (operaciones
Odoo, requiere API Key) y `/health` (estado del servicio, sin auth).

## Arquitectura
- **api.py**: Aplicacion FastAPI. Gestiona autenticacion (API Key via header `X-Api-Key`),
  validacion de whitelist (`ALLOWED_MODELS`/`ALLOWED_METHODS` desde `.env`), rate limiting
  (60 req/min por IP via slowapi), logging de auditoria y delegacion al conector Odoo.
- **odoo_universal.py**: Clase `OdooUniversalAPI` - wrapper JSON-RPC de Odoo. Maneja login,
  ejecucion, errores tipados (`OdooConnectionError`, `OdooExecutionError`) y soporte multi-tenant.
- **requirements.txt**: `fastapi`, `uvicorn[standard]`, `requests`, `pydantic`, `python-dotenv`,
  `slowapi`, `httpx`, `pytest`, `pytest-asyncio`.
- **tests/test_api.py**: Tests unitarios con mocks (sin necesidad de Odoo real).

## Peticion estandar a /odoo
```json
{
  "model": "account.move",
  "method": "search_read",
  "args": [[["state", "=", "posted"]]],
  "kwargs": {"fields": ["name", "amount_total"], "limit": 10},
  "tenant": "default"
}
```
Header obligatorio: `X-Api-Key: <valor de API_KEY en .env>`

## Variables de entorno (.env)
- `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD` - conexion Odoo
- `API_KEY` - clave de autenticacion del middleware
- `ALLOWED_MODELS` - modelos permitidos, separados por coma. Vacio = todos.
- `ALLOWED_METHODS` - metodos permitidos, separados por coma. Vacio = todos.

## Flujos de trabajo
- **Arrancar:** `uvicorn api:app --reload`
- **Tests:** `pytest tests/ -v`
- **Docker:** `docker build -t api-odoo . && docker run --env-file .env -p 8000:8000 api-odoo`
- **Agregar modelo permitido:** edita `ALLOWED_MODELS` en `.env`
- **Agregar tenant:** llama a `register_tenant(nombre, OdooUniversalAPI(...))` al inicio de `api.py`

## Manejo de errores
- `401` - API Key ausente o incorrecta
- `422` - modelo/metodo no permitido, o error de Odoo (OdooExecutionError)
- `429` - rate limit superado
- `503` - Odoo no disponible (OdooConnectionError)

## Notas
- Sigue PEP8. Comentarios en espanol.
- No commitees `.env`. Usa `.env.example` como plantilla.
- El whitelist es un no-op cuando las variables estan vacias (util para desarrollo).
- FastAPI provee documentacion interactiva en `/docs` (Swagger UI).

---
Si agregas endpoints, modelos, tenants o flujos nuevos, actualiza este archivo.
