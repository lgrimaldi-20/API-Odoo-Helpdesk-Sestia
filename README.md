# API-Odoo Middleware

Middleware universal en Python/FastAPI para conectar Odoo ERP con agentes externos
(Excel, scripts, agentes de IA, bases de datos).

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
| POST | `/odoo` | Si (X-Api-Key) | Ejecutar operacion en Odoo |
| GET | `/docs` | No | Documentacion interactiva (Swagger) |

## Codigos de respuesta

| Codigo | Significado |
|--------|-------------|
| 200 | Operacion exitosa |
| 401 | API Key invalida o ausente |
| 422 | Modelo/metodo no permitido o error de Odoo |
| 429 | Rate limit superado |
| 503 | Odoo no disponible |

## Docker

```bash
docker build -t api-odoo .
docker run --env-file .env -p 8000:8000 api-odoo
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
