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

    if not db or db.startswith("PENDIENTE"):
        print(f"{FALLO} ODOO_DB sin configurar en .env.")
        print("        Actívalo en Odoo: Ajustes -> modo desarrollador; el nombre")
        print("        de la base aparece al pie del menu de Ajustes.")
        return 1

    # 1. Conexion y login
    try:
        odoo = OdooUniversalAPI(
            url=url, db=db, username=usuario,
            password=os.getenv("ODOO_PASSWORD", ""),
        )
    except OdooConnectionError as e:
        print(f"{FALLO} No se pudo conectar: {e}")
        return 1
    print(f"{OK} Login correcto (uid={odoo.uid})")

    # 2. Modulo Helpdesk instalado
    try:
        hay_helpdesk = odoo.execute(
            "ir.model", "search_count", [[["model", "=", "helpdesk.ticket"]]]
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
            n = odoo.execute(modelo, "search_count", [[]])
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
