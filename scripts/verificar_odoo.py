"""
Verifica que el .env apunta a un Odoo usable para la exportacion de Helpdesk.

Comprueba, en orden: conexion y login, que el modulo Helpdesk este instalado,
que el usuario pueda LEER cada modelo que la exportacion necesita, y muestra los
volumenes que se exportarian. Pensado para ejecutarlo antes del QA.

Uso:
    python scripts/verificar_odoo.py

Si ODOO_DB esta mal, Odoo responde 'database "X" does not exist': el nombre de
la base NO siempre coincide con el subdominio. Se ve en Odoo activando el modo
desarrollador (Ajustes -> Herramientas de desarrollador), al pie de Ajustes.
"""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from odoo_universal import OdooConnectionError, OdooExecutionError, OdooUniversalAPI  # noqa: E402

# Modelos que la exportacion necesita leer, con lo que se usa de cada uno.
MODELOS = [
    ("helpdesk.ticket", "los tickets"),
    ("helpdesk.team", "equipos (catalogo)"),
    ("helpdesk.stage", "etapas y estado open/closed"),
    ("helpdesk.tag", "etiquetas (catalogo cerrado)"),
    ("mail.message", "historial del chatter"),
    ("mail.message.subtype", "distinguir nota interna de comentario publico"),
    ("ir.attachment", "adjuntos"),
    ("res.users", "emails de agentes"),
    ("res.partner", "emails de autores y contactos"),
    ("ir.model", "deteccion de modelos disponibles"),
]

OK, FALLO, AVISO = "[OK]  ", "[FALLO]", "[AVISO]"


def main() -> int:
    load_dotenv()
    url = os.getenv("ODOO_URL", "")
    db = os.getenv("ODOO_DB", "")
    usuario = os.getenv("ODOO_USERNAME", "")

    print(f"URL      : {url}")
    print(f"BD       : {db}")
    print(f"Usuario  : {usuario}\n")

    clave = os.getenv("ODOO_PASSWORD", "")
    pendientes = [
        nombre for nombre, valor in (("ODOO_DB", db), ("ODOO_PASSWORD", clave))
        if not valor or valor.startswith("PENDIENTE")
    ]
    if pendientes:
        print(f"{FALLO} Sin configurar en .env: {', '.join(pendientes)}")
        if "ODOO_DB" in pendientes:
            print("        BD    : Odoo -> Ajustes -> activar modo desarrollador;")
            print("                el nombre aparece al pie del menu de Ajustes.")
        if "ODOO_PASSWORD" in pendientes:
            print("        Clave : Ajustes -> Usuarios -> (tu usuario) ->")
            print("                Seguridad de la cuenta -> Claves de API.")
        return 1

    # 1. Conexion y login
    try:
        odoo = OdooUniversalAPI(
            url=url, db=db, username=usuario, password=clave,
        )
    except OdooConnectionError as e:
        # Los dos fallos tipicos dan un error de Odoo poco descriptivo: se
        # traducen a la accion concreta que hay que hacer.
        detalle = str(e)
        if "does not exist" in detalle:
            print(f"{FALLO} La base de datos '{db}' no existe en este servidor.")
            print("        Cada instancia tiene su propia BD: la de otra instancia")
            print("        (p.ej. una de Odoo.sh) no sirve aqui.")
            print("        Verla en Odoo -> Ajustes -> modo desarrollador.")
        elif "Access Denied" in detalle or "Wrong login" in detalle:
            print(f"{FALLO} Usuario o clave incorrectos para esta instancia.")
            print("        Genera una clave nueva en Ajustes -> Usuarios ->")
            print("        (tu usuario) -> Seguridad de la cuenta -> Claves de API.")
        else:
            print(f"{FALLO} No se pudo conectar: {e}")
        return 1
    print(f"{OK} Login correcto (uid={odoo.uid})")

    # 2. Modulo Helpdesk instalado
    try:
        hay_helpdesk = odoo.execute(
            "ir.model", "search_count", [["model", "=", "helpdesk.ticket"]]
        )
    except OdooExecutionError as e:
        print(f"{FALLO} No se pudo consultar ir.model: {e}")
        return 1
    if not hay_helpdesk:
        print(f"{FALLO} El modulo Helpdesk no esta instalado en esta instancia.")
        return 1
    print(f"{OK} Modulo Helpdesk instalado")

    # 3. Permisos de lectura, modelo a modelo
    print("\nPermisos de lectura:")
    faltan = []
    for modelo, para_que in MODELOS:
        try:
            n = odoo.execute(modelo, "search_count", [])
            print(f"  {OK} {modelo:24} {n:>7} registros  ({para_que})")
        except OdooExecutionError as e:
            faltan.append(modelo)
            print(f"  {FALLO} {modelo:24} SIN ACCESO  ({para_que}) -> {str(e)[:60]}")

    if faltan:
        print(f"\n{FALLO} Faltan permisos de lectura en: {', '.join(faltan)}")
        print("        La exportacion fallara o saldra incompleta.")
        return 1

    # 4. Volumenes y aviso sobre reglas de registro
    from core.helpdesk_export import contar_volumenes  # noqa: E402

    print("\nVolumenes visibles para este usuario:")
    v = contar_volumenes(odoo)
    t, m, a = v["tickets"], v["mensajes"], v["adjuntos"]
    print(f"  Tickets  : {t['total']} ({t['abiertos']} abiertos / {t['cerrados']} cerrados)")
    print(f"  Mensajes : {m['total']} ({m['comentario']} comentarios, "
          f"{m['nota_interna']} notas internas, {m['tracking']} tracking)")
    print(f"  Adjuntos : {a['total']} ({a['megabytes']} MB)")

    if t["total"] == 0:
        print(f"\n{AVISO} No hay tickets visibles. Si la instancia deberia tener,")
        print("        revisa las reglas de registro del usuario.")
    else:
        print(f"\n{AVISO} Contrasta el numero de tickets con el que ve un administrador")
        print("        en la interfaz de Odoo. Si las reglas de registro limitan a este")
        print("        usuario, la exportacion saldra incompleta SIN dar error.")

    print("\nListo para exportar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
