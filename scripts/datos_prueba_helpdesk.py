"""
Crea datos de prueba de Helpdesk en Odoo para el QA de la exportacion.

NO forma parte del middleware: es una utilidad de QA, y es lo UNICO de este
repo que escribe en Odoo. Pensado para una instancia de pruebas vacia.

Uso (en el SHELL de Odoo.sh, sobre la rama de pruebas):

    odoo-bin shell -d $PGDATABASE --no-http < datos_prueba_helpdesk.py

o pegando el contenido en `odoo-bin shell`. Tambien se puede ejecutar por RPC:

    python scripts/datos_prueba_helpdesk.py --rpc

Cada ticket ejercita un camino distinto del exportador:

  1. Abierto, con etiquetas, contacto y adjunto real   -> manifiesto, catalogos
  2. Cerrado con fecha de cierre                        -> estado closed, fecha_cierre
  3. Correo con hilo citado + imagen embebida           -> recorte de citas (5.3),
                                                           rescate de imagenes al ZIP
  4. Con nota interna y comentario publico              -> clasificacion (5.1)

Es idempotente: si los tickets ya existen (por su titulo), no los duplica.
"""

import base64

# PNG 1x1 valido, para no depender de ficheros externos.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

MARCA = "[QA-EXPORT]"  # prefijo para reconocer (y no duplicar) los datos de prueba


def crear_datos(env):
    """Crea equipo, etapas, etiquetas, contacto y 4 tickets con su chatter."""
    creados = {"tickets": [], "mensajes": 0, "adjuntos": 0}

    # --- Equipo -------------------------------------------------------------
    Equipo = env["helpdesk.team"]
    equipo = Equipo.search([("name", "=", f"{MARCA} Soporte")], limit=1)
    if not equipo:
        equipo = Equipo.create({"name": f"{MARCA} Soporte"})

    # --- Etapas: una normal y una de cierre (fold=True) ----------------------
    Etapa = env["helpdesk.stage"]
    etapa_nueva = Etapa.search([("name", "=", f"{MARCA} Nuevo")], limit=1)
    if not etapa_nueva:
        etapa_nueva = Etapa.create({
            "name": f"{MARCA} Nuevo", "sequence": 1,
            "fold": False, "team_ids": [(6, 0, [equipo.id])],
        })
    etapa_cerrada = Etapa.search([("name", "=", f"{MARCA} Resuelto")], limit=1)
    if not etapa_cerrada:
        # fold=True es lo que el exportador interpreta como "etapa de cierre".
        etapa_cerrada = Etapa.create({
            "name": f"{MARCA} Resuelto", "sequence": 90,
            "fold": True, "team_ids": [(6, 0, [equipo.id])],
        })

    # --- Etiquetas (el catalogo del modulo destino es CERRADO) ---------------
    Tag = env["helpdesk.tag"]
    etiquetas = []
    for nombre in (f"{MARCA} urgente", f"{MARCA} vip"):
        tag = Tag.search([("name", "=", nombre)], limit=1) or Tag.create({"name": nombre})
        etiquetas.append(tag.id)

    # --- Contacto del cliente ------------------------------------------------
    Partner = env["res.partner"]
    contacto = Partner.search([("email", "=", "cliente.qa@ejemplo.com")], limit=1)
    if not contacto:
        contacto = Partner.create({
            "name": f"{MARCA} Cliente Ejemplo",
            "email": "cliente.qa@ejemplo.com",
            "phone": "+34 600 111 222",
        })

    Ticket = env["helpdesk.ticket"]

    def nuevo_ticket(titulo, valores):
        """Crea el ticket si no existe ya (idempotencia por titulo)."""
        existente = Ticket.search([("name", "=", titulo)], limit=1)
        if existente:
            return existente, False
        base = {
            "name": titulo,
            "team_id": equipo.id,
            "stage_id": etapa_nueva.id,
            "partner_id": contacto.id,
        }
        base.update(valores)
        return Ticket.create(base), True

    # === Ticket 1: abierto, etiquetas, contacto y adjunto real ==============
    t1, nuevo = nuevo_ticket(f"{MARCA} 1 - No arranca el equipo", {
        "description": "<p>El equipo <b>no</b> enciende tras el corte de luz.</p>",
        "priority": "2",
        "tag_ids": [(6, 0, etiquetas)],
    })
    if nuevo:
        env["ir.attachment"].create({
            "name": "captura_error.png",
            "datas": base64.b64encode(PNG_1PX).decode(),
            "res_model": "helpdesk.ticket",
            "res_id": t1.id,
            "mimetype": "image/png",
        })
        creados["adjuntos"] += 1
    creados["tickets"].append((t1.id, "abierto + adjunto + etiquetas"))

    # === Ticket 2: cerrado, con fecha de cierre ============================
    t2, nuevo = nuevo_ticket(f"{MARCA} 2 - Duda de facturacion", {
        "description": "Consulta resuelta por telefono.",
        "priority": "0",
        "stage_id": etapa_cerrada.id,
    })
    creados["tickets"].append((t2.id, "cerrado (fold=True)"))

    # === Ticket 3: correo con hilo citado + imagen embebida ================
    t3, nuevo = nuevo_ticket(f"{MARCA} 3 - Reenvio de correo con historial", {
        "description": "Ticket abierto desde un correo del cliente.",
        "priority": "1",
    })
    if nuevo:
        # Adjunto que luego se referencia embebido en el cuerpo del mensaje.
        img = env["ir.attachment"].create({
            "name": "firma_cliente.png",
            "datas": base64.b64encode(PNG_1PX).decode(),
            "res_model": "helpdesk.ticket",
            "res_id": t3.id,
            "mimetype": "image/png",
        })
        creados["adjuntos"] += 1
        # Cuerpo tipico de correo: texto nuevo + imagen + hilo citado + firma.
        # El exportador debe quedarse SOLO con el texto nuevo y sacar la imagen
        # al ZIP de adjuntos (seccion 5.3 de la especificacion).
        t3.message_post(
            body=(
                "<p>Adjunto la captura del error, sigue fallando.</p>"
                f'<img src="/web/image/{img.id}">'
                "<blockquote>El 12/03 escribiste: hemos reiniciado el servidor,"
                " prueba de nuevo.</blockquote>"
                '<div class="gmail_signature">Un saludo,<br>Cliente Ejemplo</div>'
            ),
            message_type="email",
            subtype_xmlid="mail.mt_comment",
            author_id=contacto.id,
        )
        creados["mensajes"] += 1
    creados["tickets"].append((t3.id, "correo citado + imagen embebida"))

    # === Ticket 4: nota interna vs comentario publico =======================
    t4, nuevo = nuevo_ticket(f"{MARCA} 4 - Incidencia con seguimiento", {
        "description": "Cliente reporta lentitud en la aplicacion.",
        "priority": "3",
        "tag_ids": [(6, 0, etiquetas[:1])],
    })
    if nuevo:
        # Comentario PUBLICO: se exporta como 'comentario' y es visible al cliente.
        t4.message_post(
            body="<p>Estamos revisando el caso, le informamos en breve.</p>",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        # NOTA INTERNA: se exporta como 'nota_interna'. Si la clasificacion
        # fallara, esto acabaria visible para el cliente.
        t4.message_post(
            body="<p>Revisar el indice de la tabla de pedidos, es el cuello de botella.</p>",
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        creados["mensajes"] += 2
    creados["tickets"].append((t4.id, "comentario publico + nota interna"))

    return creados


def _resumen(env, creados):
    print("\n=== Datos de prueba de Helpdesk ===")
    for tid, que in creados["tickets"]:
        print(f"  ticket {tid:>4}  {que}")
    print(f"\n  mensajes creados : {creados['mensajes']}")
    print(f"  adjuntos creados : {creados['adjuntos']}")
    print(f"  TOTAL en la base : {env['helpdesk.ticket'].search_count([])} tickets")
    print("\nSiguiente paso:  python scripts/verificar_odoo.py")


def main_rpc():
    """Ejecuta el mismo alta por JSON-RPC, usando el .env (sin shell de Odoo.sh)."""
    import os
    import sys

    from dotenv import load_dotenv

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    load_dotenv()
    from odoo_universal import OdooUniversalAPI

    odoo = OdooUniversalAPI(
        url=os.getenv("ODOO_URL", ""), db=os.getenv("ODOO_DB", ""),
        username=os.getenv("ODOO_USERNAME", ""), password=os.getenv("ODOO_PASSWORD", ""),
    )
    print(f"Conectado a {os.getenv('ODOO_DB')} (uid={odoo.uid})")
    entorno = _EnvRPC(odoo)
    creados = crear_datos(entorno)
    _resumen(entorno, creados)


class _RecordsetRPC:
    """Recordset minimo sobre JSON-RPC, con lo que usa crear_datos()."""

    def __init__(self, odoo, modelo, ids=()):
        self._odoo, self._modelo, self.ids = odoo, modelo, list(ids)

    def __bool__(self):
        return bool(self.ids)

    def __getitem__(self, i):
        return _RecordsetRPC(self._odoo, self._modelo, [self.ids[i]])

    @property
    def id(self):
        return self.ids[0] if self.ids else False

    def search(self, dominio, limit=None):
        kwargs = {"limit": limit} if limit else {}
        ids = self._odoo.execute(self._modelo, "search", dominio, **kwargs)
        return _RecordsetRPC(self._odoo, self._modelo, ids)

    def search_count(self, dominio):
        return self._odoo.execute(self._modelo, "search_count", dominio)

    def create(self, valores):
        nuevo = self._odoo.execute(self._modelo, "create", valores)
        return _RecordsetRPC(self._odoo, self._modelo, [nuevo])

    def __or__(self, otro):
        return self if self.ids else otro

    def message_post(self, **kwargs):
        """
        Publica en el chatter. Por RPC, message_post ESCAPA el html del `body`
        (lo guarda como &lt;p&gt;...), asi que se crea el mail.message directo
        para que el cuerpo quede como HTML real: es justo lo que el exportador
        tiene que limpiar en produccion.
        """
        cuerpo = kwargs.pop("body", "")
        subtipo = kwargs.pop("subtype_xmlid", "mail.mt_comment")
        subtype_id = self._odoo.execute(
            "ir.model.data", "check_object_reference", *subtipo.split(".")
        )[1]
        valores = {
            "model": self._modelo,
            "res_id": self.id,
            "body": cuerpo,
            "message_type": kwargs.pop("message_type", "comment"),
            "subtype_id": subtype_id,
        }
        if kwargs.get("author_id"):
            valores["author_id"] = kwargs["author_id"]
        return self._odoo.execute("mail.message", "create", valores)


class _EnvRPC:
    """env[...] equivalente por RPC, para reutilizar crear_datos() sin cambios."""

    def __init__(self, odoo):
        self._odoo = odoo

    def __getitem__(self, modelo):
        return _RecordsetRPC(self._odoo, modelo)


if __name__ == "__main__":
    import sys

    if "--rpc" in sys.argv:
        main_rpc()
    else:
        print(__doc__)
        print("Ejecutalo con --rpc, o pegalo en `odoo-bin shell` de Odoo.sh.")
else:
    # Ejecutado dentro de `odoo-bin shell`, donde `env` ya existe.
    try:
        _resumen(env, crear_datos(env))  # noqa: F821
        env.cr.commit()  # noqa: F821
        print("\nCambios confirmados (commit).")
    except NameError:
        pass
