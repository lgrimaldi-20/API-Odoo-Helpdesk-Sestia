# Sincronizacion ETL: Odoo > PostgreSQL / MySQL

## Flujo recomendado

```
Odoo (JSON-RPC)
    |  [middleware /odoo]
API-Odoo Middleware
    |  [script Python]
Base de datos destino (PostgreSQL o MySQL)
```

## Pasos del flujo

1. **Extraccion**: El script llama al middleware con `search_read` y paginacion.
2. **Transformacion**: Aplana campos relacionales (`partner_id` > `partner_id`, `partner_name`).
3. **Carga**: Hace upsert en la tabla destino usando el `id` de Odoo como clave.
4. **Registro**: Guarda la ultima fecha de sincronizacion para extracciones incrementales.

## Script de ejemplo

```python
import os
import requests
import psycopg2  # o mysql.connector para MySQL

API_URL = os.getenv("API_URL", "http://localhost:8000/odoo")
API_KEY = os.getenv("API_KEY", "")
DB_DSN  = os.getenv("DB_DSN", "postgresql://user:pass@localhost/odoo_sync")

MODELOS_A_SINCRONIZAR = [
    {
        "odoo_model": "account.move",
        "tabla_destino": "facturas",
        "campos": ["id", "name", "state", "amount_total", "invoice_date"],
        "filtro_base": [["move_type", "=", "out_invoice"]],
    },
    {
        "odoo_model": "res.partner",
        "tabla_destino": "contactos",
        "campos": ["id", "name", "email", "phone", "vat"],
        "filtro_base": [["active", "=", True]],
    },
]

BATCH_SIZE = 500


def extraer_odoo(model, campos, filtros, offset=0):
    """Extrae un lote de registros de Odoo via el middleware."""
    r = requests.post(
        API_URL,
        json={
            "model": model,
            "method": "search_read",
            "args": [filtros],
            "kwargs": {"fields": campos, "limit": BATCH_SIZE, "offset": offset},
        },
        headers={"X-Api-Key": API_KEY},
    )
    r.raise_for_status()
    return r.json()["result"]


def sincronizar(config, conn):
    """Sincroniza un modelo de Odoo a una tabla destino con paginacion."""
    offset = 0
    total = 0
    while True:
        registros = extraer_odoo(
            config["odoo_model"],
            config["campos"],
            config["filtro_base"],
            offset,
        )
        if not registros:
            break

        # --- Carga: adapta esta seccion a tu motor de BD ---
        cur = conn.cursor()
        for reg in registros:
            columnas = ", ".join(reg.keys())
            placeholders = ", ".join(["%s"] * len(reg))
            valores = list(reg.values())
            cur.execute(
                f"INSERT INTO {config['tabla_destino']} ({columnas}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT (id) DO UPDATE SET "
                + ", ".join(f"{k} = EXCLUDED.{k}" for k in reg.keys() if k != "id"),
                valores,
            )
        conn.commit()

        total += len(registros)
        offset += BATCH_SIZE

    print(f"  {config['odoo_model']}: {total} registros sincronizados")


def main():
    conn = psycopg2.connect(DB_DSN)
    for config in MODELOS_A_SINCRONIZAR:
        print(f"Sincronizando {config['odoo_model']}...")
        sincronizar(config, conn)
    conn.close()
    print("Sincronizacion completa.")


if __name__ == "__main__":
    main()
```

## Variables de entorno necesarias

```
API_URL=http://localhost:8000/odoo
API_KEY=tu-clave
DB_DSN=postgresql://user:pass@localhost/odoo_sync
```

## Automatizacion

Para ejecutar la sincronizacion periodicamente:

- **Linux (cron)**: `0 */6 * * * cd /ruta/proyecto && python scripts/etl_sync.py`
- **Windows (Task Scheduler)**: Crea una tarea que ejecute el script cada N horas.
- **Docker**: Agrega un servicio con `command: python scripts/etl_sync.py` y reinicio programado.
