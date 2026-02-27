# Ejemplos de Integracion con Agentes

## Python

### Buscar facturas posteadas

```python
import requests

API_URL = "http://localhost:8000/odoo"
API_KEY = "tu-clave"

def consultar_odoo(model, method, args=None, kwargs=None):
    response = requests.post(
        API_URL,
        json={
            "model": model,
            "method": method,
            "args": args or [],
            "kwargs": kwargs or {},
        },
        headers={"X-Api-Key": API_KEY},
    )
    response.raise_for_status()
    return response.json()["result"]

# Ejemplo: facturas posteadas
facturas = consultar_odoo(
    model="account.move",
    method="search_read",
    args=[[["state", "=", "posted"], ["move_type", "=", "out_invoice"]]],
    kwargs={"fields": ["name", "amount_total", "invoice_date"], "limit": 50},
)
print(facturas)
```

### Crear un contacto

```python
nuevo_id = consultar_odoo(
    model="res.partner",
    method="create",
    args=[{"name": "Empresa XYZ", "email": "contacto@xyz.com"}],
)
print(f"Contacto creado con ID: {nuevo_id}")
```

## Node.js / JavaScript

```javascript
const API_URL = 'http://localhost:8000/odoo';
const API_KEY = 'tu-clave';

async function consultarOdoo(model, method, args = [], kwargs = {}) {
  const response = await fetch(API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Api-Key': API_KEY,
    },
    body: JSON.stringify({ model, method, args, kwargs }),
  });
  if (!response.ok) {
    throw new Error(`Error ${response.status}: ${await response.text()}`);
  }
  return (await response.json()).result;
}

// Ejemplo: listar 10 pedidos de venta confirmados
const pedidos = await consultarOdoo(
  'sale.order',
  'search_read',
  [[['state', '=', 'sale']]],
  { fields: ['name', 'partner_id', 'amount_total'], limit: 10 }
);
console.log(pedidos);
```

## curl (linea de comandos / scripts bash)

```bash
curl -s -X POST http://localhost:8000/odoo \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: tu-clave" \
  -d '{
    "model": "res.partner",
    "method": "search_read",
    "args": [[["customer_rank", ">", 0]]],
    "kwargs": {"fields": ["name", "email"], "limit": 5}
  }' | python3 -m json.tool
```

## Codigos de error comunes

| Codigo | Significado | Solucion |
|--------|-------------|----------|
| 401 | API Key invalida o ausente | Agrega el header `X-Api-Key` |
| 422 | Modelo/metodo no permitido o error de Odoo | Revisa ALLOWED_MODELS/METHODS en .env |
| 429 | Rate limit superado (60 req/min) | Espera antes de reintentar |
| 503 | Odoo no disponible | Verifica la conexion con `/health` |
