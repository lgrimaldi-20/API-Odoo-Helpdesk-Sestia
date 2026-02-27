"""
Busca y elimina contactos duplicados en Odoo creados durante las pruebas.

Para cada NIF de la lista de clientes de prueba:
  - Busca todos los registros con ese NIF en Odoo
  - Si hay mas de uno, conserva el de ID mas bajo y elimina el resto

Uso: python scripts/borrar_duplicados.py [--dry-run]
  --dry-run  Muestra que se borraria sin ejecutar nada en Odoo
"""

import os
import sys
import requests
from collections import defaultdict

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

API_URL = os.getenv("API_URL", "http://localhost:8000/odoo")
API_KEY = os.getenv("API_KEY", "")

if not API_KEY:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("API_KEY="):
                    API_KEY = line.split("=", 1)[1]
                    break

DRY_RUN = "--dry-run" in sys.argv

# NIFs de los clientes de prueba (de crear_clientes_prueba.py)
NIFS_PRUEBA = [
    "B11111111",  # TechSmart Solutions SL
    "A22222222",  # Innovatech Group SA
    "B33333333",  # DataFlow Consulting SL
    "A44444444",  # CloudPeak Services SA
    "B55555555",  # NexGen Digital SL
    "A66666666",  # Bright Analytics SA
    "B77777777",  # SmartBridge Corp SL
    "A88888888",  # Vertex Systems SA
    "B99999999",  # PulseCode Solutions SL
    "A10101010",  # ElevateAI Technologies SA
    "B10101011",  # Real Madrid CF
]


# ---------------------------------------------------------------------------
# Comunicacion con el middleware
# ---------------------------------------------------------------------------

def enviar_a_odoo(model, method, args=None, kwargs=None):
    response = requests.post(
        API_URL,
        json={"model": model, "method": method, "args": args or [], "kwargs": kwargs or {}},
        headers={"X-Api-Key": API_KEY},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["result"]


# ---------------------------------------------------------------------------
# Logica de limpieza
# ---------------------------------------------------------------------------

def buscar_por_nifs(nifs):
    """Devuelve todos los registros de res.partner que tengan alguno de los NIFs."""
    return enviar_a_odoo(
        "res.partner",
        "search_read",
        args=[[["vat", "in", nifs]]],
        kwargs={"fields": ["id", "name", "vat"], "order": "id asc"},
    )


def calcular_duplicados(registros):
    """
    Agrupa por NIF y detecta duplicados.
    Retorna lista de IDs a eliminar (conserva el ID mas bajo de cada NIF).
    """
    por_nif = defaultdict(list)
    for r in registros:
        if r.get("vat"):
            por_nif[r["vat"]].append(r)

    a_borrar = []
    resumen = []

    for nif, grupo in por_nif.items():
        # Ordenar por ID para conservar el mas antiguo (menor ID)
        grupo.sort(key=lambda x: x["id"])
        conservar = grupo[0]
        duplicados = grupo[1:]

        if duplicados:
            resumen.append({
                "nif": nif,
                "conservar": conservar,
                "duplicados": duplicados,
            })
            a_borrar.extend([d["id"] for d in duplicados])

    return a_borrar, resumen


def mostrar_resumen(resumen, a_borrar):
    if not resumen:
        print("  No se encontraron duplicados. Todo limpio.")
        return

    print(f"\n  Duplicados encontrados:\n")
    for item in resumen:
        conservar = item["conservar"]
        print(f"  NIF: {item['nif']}")
        print(f"    Conservar : ID={conservar['id']:>6}  {conservar['name']}")
        for dup in item["duplicados"]:
            print(f"    Eliminar  : ID={dup['id']:>6}  {dup['name']}")
        print()

    print(f"  Total a eliminar: {len(a_borrar)} registro(s)")


def borrar_ids(ids_a_borrar):
    """Llama a res.partner.unlink() para eliminar los IDs indicados."""
    try:
        result = enviar_a_odoo(
            "res.partner",
            "unlink",
            args=[ids_a_borrar],
        )
        return result
    except Exception as e:
        raise RuntimeError(f"Error al eliminar: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  BORRAR DUPLICADOS DE CLIENTES DE PRUEBA")
    if DRY_RUN:
        print("  [MODO DRY-RUN - no se ejecutara ningun borrado]")
    print("=" * 60)
    print(f"\nMiddleware: {API_URL}")
    print(f"NIFs a revisar: {len(NIFS_PRUEBA)}\n")

    # 1. Buscar todos los registros con los NIFs de prueba
    print("Buscando registros en Odoo...")
    try:
        registros = buscar_por_nifs(NIFS_PRUEBA)
    except Exception as e:
        print(f"  ERROR al consultar Odoo: {e}")
        sys.exit(1)

    print(f"  Registros encontrados: {len(registros)}")

    if not registros:
        print("  Ninguno de los NIFs de prueba existe en Odoo.")
        return

    # 2. Calcular cuales son duplicados
    a_borrar, resumen = calcular_duplicados(registros)
    mostrar_resumen(resumen, a_borrar)

    if not a_borrar:
        return

    # 3. Confirmar y borrar
    if DRY_RUN:
        print("\n[DRY-RUN] No se ha borrado nada. Quita --dry-run para ejecutar.")
        return

    confirmacion = input("\nConfirmar borrado? [s/N]: ").strip().lower()
    if confirmacion not in ("s", "si", "y", "yes"):
        print("Operacion cancelada.")
        return

    print(f"\nEliminando {len(a_borrar)} registro(s)...")
    try:
        borrar_ids(a_borrar)
        print(f"  OK - {len(a_borrar)} duplicado(s) eliminados correctamente.")
    except RuntimeError as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    print("\nListo! Verifica los contactos en tu Odoo.")


if __name__ == "__main__":
    main()
