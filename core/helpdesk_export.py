"""
Exportacion de tickets de Odoo Helpdesk hacia el modulo Helpdesk de SESTIA.

Este modulo NO escribe en Odoo: es la cara de SOLO LECTURA del middleware.
Produce los tres archivos que la especificacion de migracion pide al equipo de
Odoo, mas los catalogos previos:

  1. tickets.csv          -> un helpdesk.ticket por fila (seccion 3 del doc).
  2. historial.jsonl      -> un mail.message del chatter por linea (seccion 5).
  3. adjuntos (manifiesto + binarios) -> ir.attachment por ticket/mensaje (seccion 6).
  4. catalogos            -> valores unicos de equipos, etapas, categorias,
                             etiquetas y usuarios (seccion 4).

Decisiones de mapeo (acordadas con el equipo SESTIA):
  - estado (open/closed) se deriva del campo `fold` de la etapa (helpdesk.stage):
    las etapas plegadas en el kanban son las de cierre. Se puede forzar con una
    lista explicita de nombres (parametro etapas_cierre / HELPDESK_ETAPAS_CIERRE).
  - Fechas en ISO 8601. Odoo las devuelve en UTC naive ('2024-03-15 10:30:00');
    las emitimos con sufijo 'Z' para que lleven offset explicito, como pide el doc.
  - El cuerpo del mensaje (HTML en Odoo) se entrega en texto plano (opcion (a)
    preferida en la seccion 5.3): se limpia el HTML a texto.
  - Las imagenes embebidas en el cuerpo (<img src="/web/image/N">) mueren al
    apagar Odoo: se detectan y se promueven a adjuntos del ZIP (seccion 5.3).
  - Los correos arrastran el hilo citado y la firma: se recortan por defecto
    (seccion 5.3), desactivable con recortar_citas=False.

Ventana de exportacion (paso 8 del plan: corte y re-exportacion incremental):
todas las funciones aceptan `desde`/`hasta` sobre write_date del ticket, de modo
que una segunda pasada solo trae lo creado o modificado tras la fecha de corte.
El odoo_ref evita duplicados en el destino.

Como inventario.py, este modulo tiene un esquema fijo y NO usa mappings.yaml.
No toca el state store: la exportacion es de solo lectura e idempotente por
naturaleza (el odoo_ref del destino evita duplicados al re-importar).
"""

import base64
import csv
import io
import json
import logging
import re
import zipfile
from html import unescape
from html.parser import HTMLParser

from odoo_universal import OdooExecutionError, OdooUniversalAPI

logger = logging.getLogger("api-odoo")


class HelpdeskExportError(Exception):
    """Fallo al exportar datos de Helpdesk desde Odoo."""
    pass


# ---------------------------------------------------------------------------
# Utilidades de formato
# ---------------------------------------------------------------------------

# Cabeceras de tickets.csv en el orden y con los nombres exactos de la seccion 3.
COLUMNAS_TICKETS = [
    "odoo_ref",
    "titulo",
    "descripcion",
    "equipo",
    "etapa",
    "prioridad",
    "estado",
    "categoria",
    "subcategoria",
    "etiquetas",
    "asignado_email",
    "creado_por_email",
    "contacto_nombre",
    "contacto_telefono",
    "contacto_email",
    "fecha_creacion",
    "fecha_cierre",
    "sla_vencimiento",
]

# Columnas del manifiesto de adjuntos (seccion 6).
COLUMNAS_MANIFIESTO = [
    "odoo_ref",
    "odoo_message_id",
    "odoo_attachment_id",
    "ruta_en_zip",
    "nombre_archivo",
    "mimetype",
    "size_bytes",
    "fecha",
    "subido_por_email",
]


class _ExtractorTexto(HTMLParser):
    """
    Convierte HTML a texto plano conservando saltos de parrafo basicos.

    Odoo marca el hilo citado de los correos con contenedores propios
    (`gmail_quote`, `o_mail_quote`, `<blockquote>`); cuando `recortar_citas` esta
    activo se ignora todo lo que haya dentro de ellos, que es justo lo que la
    seccion 5.3 pide descartar (hilo anterior y firmas).
    """

    _BLOQUE = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
    # Clases con las que Odoo/los clientes de correo envuelven el hilo citado.
    _CLASES_CITA = ("gmail_quote", "o_mail_quote", "moz-cite-prefix", "gmail_signature")

    def __init__(self, recortar_citas: bool = False):
        super().__init__()
        self._partes: list[str] = []
        self._recortar = recortar_citas
        # Profundidad dentro de un bloque citado; >0 significa "no capturar".
        self._en_cita = 0

    def _es_cita(self, tag, attrs) -> bool:
        if tag == "blockquote":
            return True
        atributos = dict(attrs)
        clases = (atributos.get("class") or "").lower()
        if any(c in clases for c in self._CLASES_CITA):
            return True
        # Odoo marca la firma del usuario con data-o-mail-quote en el nodo.
        return "data-o-mail-quote" in atributos

    def handle_starttag(self, tag, attrs):
        if self._recortar and self._es_cita(tag, attrs):
            self._en_cita += 1
            return
        if self._en_cita:
            return
        if tag in self._BLOQUE:
            self._partes.append("\n")

    def handle_endtag(self, tag):
        if self._en_cita:
            # Solo el cierre del propio contenedor de cita reduce la profundidad.
            if tag in ("blockquote", "div", "span", "p"):
                self._en_cita = max(0, self._en_cita - 1)
            return
        if tag in self._BLOQUE:
            self._partes.append("\n")

    def handle_data(self, data):
        if self._en_cita:
            return
        self._partes.append(data)

    def texto(self) -> str:
        crudo = "".join(self._partes)
        # Colapsa espacios en cada linea y elimina lineas vacias sobrantes.
        lineas = [re.sub(r"[ \t]+", " ", ln).strip() for ln in crudo.splitlines()]
        return "\n".join(ln for ln in lineas if ln).strip()


def html_a_texto(html: str, recortar_citas: bool = False) -> str:
    """
    Limpia el HTML de Odoo a texto plano (opcion (a) del doc, seccion 5.3).

    recortar_citas: descarta el hilo citado y las firmas de los correos
    (blockquote / gmail_quote / o_mail_quote), como sugiere la seccion 5.3.

    Tolerante: ante HTML malformado cae a un simple strip de etiquetas.
    """
    if not html:
        return ""
    try:
        parser = _ExtractorTexto(recortar_citas=recortar_citas)
        parser.feed(html)
        parser.close()
        return parser.texto()
    except Exception:  # HTML muy roto: quita etiquetas de forma basica
        return unescape(re.sub(r"<[^>]+>", " ", html)).strip()


# Imagenes embebidas en el cuerpo: <img src="/web/image/1234"> (adjunto de Odoo)
# o <img src="data:image/png;base64,...."> (binario inline). Ambas rutas mueren
# al apagar Odoo, asi que la seccion 5.3 pide sacarlas como adjuntos del ZIP.
_RE_IMG_SRC = re.compile(r"<img[^>]*\ssrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
_RE_WEB_IMAGE = re.compile(r"/web/image/(?:ir\.attachment/)?(\d+)", re.IGNORECASE)
_RE_DATA_URI = re.compile(
    r"^data:([\w.+-]+/[\w.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL
)


def imagenes_embebidas(html: str) -> dict:
    """
    Extrae las imagenes embebidas en el cuerpo de un mensaje (seccion 5.3).

    Devuelve {"attachment_ids": [ids de ir.attachment referenciados por
    /web/image/N], "inline": [{"mimetype", "datos_b64"}] para las data: URI}.
    """
    if not html:
        return {"attachment_ids": [], "inline": []}
    ids: list[int] = []
    inline: list[dict] = []
    for src in _RE_IMG_SRC.findall(html):
        web = _RE_WEB_IMAGE.search(src)
        if web:
            ids.append(int(web.group(1)))
            continue
        datos = _RE_DATA_URI.match(src.strip())
        if datos:
            inline.append({
                "mimetype": datos.group(1),
                "datos_b64": re.sub(r"\s+", "", datos.group(2)),
            })
    # Sin duplicados, conservando el orden de aparicion.
    vistos: set[int] = set()
    ids_unicos = [i for i in ids if not (i in vistos or vistos.add(i))]
    return {"attachment_ids": ids_unicos, "inline": inline}


# ---------------------------------------------------------------------------
# Ventana de exportacion (fecha de corte / re-exportacion incremental)
# ---------------------------------------------------------------------------

def _a_fecha_odoo(valor: str) -> str:
    """
    Convierte una fecha ISO 8601 al formato naive que espera Odoo
    ('YYYY-MM-DD HH:MM:SS'). Acepta 'YYYY-MM-DD' (se completa a medianoche).
    """
    texto = str(valor).strip()
    # Quita el offset/Z: Odoo compara contra datetimes UTC naive.
    texto = re.sub(r"([zZ]|[+-]\d\d:?\d\d)$", "", texto).strip()
    texto = texto.replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
        texto += " 00:00:00"
    return texto


def _dominio_tickets(desde: str | None = None, hasta: str | None = None) -> list:
    """
    Construye el dominio de busqueda de tickets con la ventana de fechas.

    Filtra por `write_date` (no `create_date`) para que la re-exportacion
    incremental del paso 8 traiga tambien los tickets MODIFICADOS despues de la
    fecha de corte, no solo los creados. El odoo_ref evita duplicados al importar.
    """
    dominio = []
    if desde:
        dominio.append(["write_date", ">=", _a_fecha_odoo(desde)])
    if hasta:
        dominio.append(["write_date", "<=", _a_fecha_odoo(hasta)])
    return dominio


def fecha_iso(valor) -> str:
    """
    Normaliza una fecha de Odoo a ISO 8601 con offset explicito.
    Odoo entrega UTC naive ('2024-03-15 10:30:00'); anadimos 'Z' (UTC).
    Devuelve cadena vacia si no hay valor.
    """
    if not valor:
        return ""
    texto = str(valor).strip()
    if not texto:
        return ""
    # 'YYYY-MM-DD HH:MM:SS' -> 'YYYY-MM-DDTHH:MM:SSZ'
    iso = texto.replace(" ", "T")
    if "T" in iso and not re.search(r"[zZ]|[+-]\d\d:?\d\d$", iso):
        iso += "Z"
    return iso


def _nombre_de_m2o(valor) -> str:
    """Extrae el nombre de un campo many2one de Odoo (formato [id, 'nombre'])."""
    if isinstance(valor, (list, tuple)) and len(valor) == 2:
        return valor[1] or ""
    return ""


def _id_de_m2o(valor):
    """Extrae el id de un campo many2one de Odoo (formato [id, 'nombre'])."""
    if isinstance(valor, (list, tuple)) and len(valor) == 2:
        return valor[0]
    return None


# ---------------------------------------------------------------------------
# Deteccion del modelo de Helpdesk disponible
# ---------------------------------------------------------------------------

def _detectar_modelo_ticket(odoo: OdooUniversalAPI) -> str:
    """
    Devuelve el modelo de ticket disponible en la instancia. El Helpdesk oficial
    usa 'helpdesk.ticket'; algunas instalaciones usan el modulo Project como
    mesa de ayuda ('project.task'). Preferimos helpdesk.ticket.
    """
    try:
        existe = odoo.execute(
            "ir.model", "search_count", [[["model", "=", "helpdesk.ticket"]]]
        )
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudo consultar los modelos de Odoo: {e}") from e
    if not existe:
        raise HelpdeskExportError(
            "El modelo 'helpdesk.ticket' no existe en esta instancia de Odoo. "
            "El modulo Helpdesk no esta instalado."
        )
    return "helpdesk.ticket"


# ---------------------------------------------------------------------------
# Etapas de cierre (para derivar estado open/closed)
# ---------------------------------------------------------------------------

def _mapa_etapas_cierre(
    odoo: OdooUniversalAPI, etapas_cierre: list[str] | None
) -> dict[int, bool]:
    """
    Devuelve {stage_id: es_de_cierre}.

    Por defecto usa el campo `fold` de helpdesk.stage (las etapas plegadas en el
    kanban son las de cierre: Resuelto, Cancelado...). Si se pasa una lista de
    nombres explicita (etapas_cierre), esa lista manda sobre `fold`.
    """
    try:
        etapas = odoo.execute(
            "helpdesk.stage", "search_read", [[]], fields=["id", "name", "fold"]
        )
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudieron leer las etapas: {e}") from e

    nombres_cierre = {n.strip().lower() for n in (etapas_cierre or []) if n.strip()}
    mapa = {}
    for etapa in etapas:
        if nombres_cierre:
            es_cierre = (etapa.get("name") or "").strip().lower() in nombres_cierre
        else:
            es_cierre = bool(etapa.get("fold"))
        mapa[etapa["id"]] = es_cierre
    return mapa


# ---------------------------------------------------------------------------
# Resolucion de emails de usuarios (res.users -> login/email)
# ---------------------------------------------------------------------------

def _cache_email_usuarios(odoo: OdooUniversalAPI, ids: set[int]) -> dict[int, str]:
    """
    Devuelve {user_id: email}. El mapeo a usuarios de SESTIA se hace por email,
    asi que resolvemos el email real de cada res.users referenciado.
    """
    ids = {i for i in ids if i}
    if not ids:
        return {}
    try:
        usuarios = odoo.execute(
            "res.users", "read", list(ids), fields=["id", "login", "email"]
        )
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudieron leer los usuarios: {e}") from e
    # login suele ser el email en Odoo; si no, cae al campo email.
    return {u["id"]: (u.get("email") or u.get("login") or "") for u in usuarios}


# ---------------------------------------------------------------------------
# 1. tickets.csv
# ---------------------------------------------------------------------------

def _campos_personalizados(campos_disponibles: dict) -> list[str]:
    """
    Detecta los campos personalizados del ticket (seccion 3: "una columna por
    campo" al final del CSV). En Odoo los campos anadidos por Studio o por un
    modulo del cliente se llaman `x_...`; solo tomamos los de tipo simple, no
    relacionales ni binarios (esos no caben en una celda de CSV).
    """
    tipos_simples = {"char", "text", "integer", "float", "boolean", "date",
                     "datetime", "selection", "monetary", "html"}
    return sorted(
        nombre for nombre, meta in (campos_disponibles or {}).items()
        if nombre.startswith("x_") and (meta or {}).get("type") in tipos_simples
    )


def _valor_personalizado(valor):
    """Normaliza el valor de un campo personalizado para una celda de CSV."""
    if valor is False or valor is None:
        return ""
    if isinstance(valor, str) and "<" in valor and ">" in valor:
        return html_a_texto(valor)  # campos html del cliente
    return valor


def _leer_tickets(
    odoo: OdooUniversalAPI,
    limite: int | None,
    etapas_cierre: list[str] | None,
    desde: str | None = None,
    hasta: str | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Lee los tickets de Odoo y los traduce al esquema de tickets.csv.

    Devuelve (filas, columnas_personalizadas) — las personalizadas se anaden al
    final de la cabecera, como pide la seccion 3.
    """
    modelo = _detectar_modelo_ticket(odoo)
    mapa_cierre = _mapa_etapas_cierre(odoo, etapas_cierre)

    # Campos opcionales segun la instalacion (categoria puede no existir).
    campos_disponibles = odoo.execute(modelo, "fields_get", [], attributes=["type"])
    tiene = lambda campo: campo in campos_disponibles  # noqa: E731

    campos = [
        "id", "name", "description", "team_id", "stage_id", "priority",
        "tag_ids", "user_id", "create_uid", "create_date", "close_date",
    ]
    for opcional in ("ticket_ref", "partner_name", "partner_email", "partner_phone",
                     "partner_id", "sla_deadline", "category_id", "subcategory_id"):
        if tiene(opcional):
            campos.append(opcional)

    personalizados = _campos_personalizados(campos_disponibles)
    campos.extend(personalizados)

    try:
        registros = odoo.execute(
            modelo, "search_read", [_dominio_tickets(desde, hasta)],
            fields=campos, limit=(limite or 0), order="id",
        )
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudieron leer los tickets: {e}") from e

    # Resolver emails de asignados y creadores en un solo lote.
    ids_usuarios: set[int] = set()
    for r in registros:
        ids_usuarios.add(_id_de_m2o(r.get("user_id")))
        ids_usuarios.add(_id_de_m2o(r.get("create_uid")))
    emails = _cache_email_usuarios(odoo, ids_usuarios)

    # Resolver nombres de etiquetas (tag_ids es una lista de ids).
    ids_tags = {tid for r in registros for tid in (r.get("tag_ids") or [])}
    nombres_tags = _leer_nombres(odoo, "helpdesk.tag", ids_tags)

    filas = []
    sin_fecha_cierre = []
    for r in registros:
        stage_id = _id_de_m2o(r.get("stage_id"))
        estado = "closed" if mapa_cierre.get(stage_id) else "open"
        etiquetas = ";".join(
            nombres_tags.get(tid, "") for tid in (r.get("tag_ids") or [])
            if nombres_tags.get(tid)
        )
        # odoo_ref: preferimos el numero visible (ticket_ref) y si no el id.
        odoo_ref = r.get("ticket_ref") or r["id"]
        # Contacto: campos partner_* directos o el partner_id relacionado.
        contacto_nombre = r.get("partner_name") or _nombre_de_m2o(r.get("partner_id"))

        fecha_cierre = fecha_iso(r.get("close_date"))
        # La seccion 3 marca fecha_cierre obligatoria en tickets cerrados. Si el
        # ticket esta en etapa de cierre y Odoo no tiene close_date, avisamos:
        # es un dato que el importador de SESTIA va a rechazar.
        if estado == "closed" and not fecha_cierre:
            sin_fecha_cierre.append(odoo_ref)

        fila = {
            "odoo_ref": odoo_ref,
            "titulo": r.get("name") or "",
            "descripcion": html_a_texto(r.get("description") or ""),
            "equipo": _nombre_de_m2o(r.get("team_id")),
            "etapa": _nombre_de_m2o(r.get("stage_id")),
            "prioridad": r.get("priority") or "0",
            "estado": estado,
            "categoria": _nombre_de_m2o(r.get("category_id")),
            "subcategoria": _nombre_de_m2o(r.get("subcategory_id")),
            "etiquetas": etiquetas,
            "asignado_email": emails.get(_id_de_m2o(r.get("user_id")), ""),
            "creado_por_email": emails.get(_id_de_m2o(r.get("create_uid")), ""),
            "contacto_nombre": contacto_nombre,
            "contacto_telefono": r.get("partner_phone") or "",
            "contacto_email": r.get("partner_email") or "",
            "fecha_creacion": fecha_iso(r.get("create_date")),
            "fecha_cierre": fecha_cierre,
            "sla_vencimiento": fecha_iso(r.get("sla_deadline")),
        }
        for campo in personalizados:
            fila[campo] = _valor_personalizado(r.get(campo))
        filas.append(fila)

    if sin_fecha_cierre:
        logger.warning(
            "HELPDESK_EXPORT | %d ticket(s) cerrados sin fecha_cierre: %s",
            len(sin_fecha_cierre),
            ", ".join(str(x) for x in sin_fecha_cierre[:20]),
        )
    return filas, personalizados


def _leer_nombres(odoo: OdooUniversalAPI, modelo: str, ids: set[int]) -> dict[int, str]:
    """Devuelve {id: name} para un modelo con campo name."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    try:
        regs = odoo.execute(modelo, "read", list(ids), fields=["id", "name"])
    except OdooExecutionError:
        return {}
    return {r["id"]: r.get("name") or "" for r in regs}


def exportar_tickets_csv(
    odoo: OdooUniversalAPI,
    limite: int | None = None,
    etapas_cierre: list[str] | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> str:
    """
    Genera el contenido de tickets.csv (UTF-8, RFC 4180) como cadena.

    limite: numero maximo de tickets (None = todos). Util para la muestra.
    etapas_cierre: nombres de etapas consideradas de cierre (override de `fold`).
    desde/hasta: ventana sobre write_date para la re-exportacion incremental.
    """
    filas, personalizados = _leer_tickets(odoo, limite, etapas_cierre, desde, hasta)
    buffer = io.StringIO()
    # Los campos personalizados van al final de la cabecera (seccion 3).
    columnas = COLUMNAS_TICKETS + personalizados
    # QUOTE_MINIMAL + quoting de saltos de linea = RFC 4180.
    writer = csv.DictWriter(
        buffer, fieldnames=columnas, quoting=csv.QUOTE_MINIMAL,
        extrasaction="ignore", lineterminator="\r\n",
    )
    writer.writeheader()
    writer.writerows(filas)
    logger.info("HELPDESK_EXPORT | tickets exportados=%d", len(filas))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 2. historial.jsonl
# ---------------------------------------------------------------------------

def _ids_tickets(
    odoo: OdooUniversalAPI,
    limite: int | None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list[int]:
    """
    Devuelve los ids de los tickets a exportar (respeta el limite de muestra y
    la ventana de fechas). Mismo dominio y mismo `order="id"` que _leer_tickets,
    para que los tres archivos cubran EL MISMO conjunto de tickets.
    """
    modelo = _detectar_modelo_ticket(odoo)
    ids = odoo.execute(
        modelo, "search", [_dominio_tickets(desde, hasta)],
        limit=(limite or 0), order="id",
    )
    return ids


def _clasificar_mensaje(msg: dict, subtipos_cierre: set[int]) -> str:
    """
    Clasifica un mail.message en comentario | nota_interna | tracking (seccion 5.1).

    - message_type == 'notification' con tracking_value_ids -> tracking.
    - subtype_id que es 'Note' (nota interna) -> nota_interna.
    - resto (comentarios y correos publicos) -> comentario.
    """
    if msg.get("tracking_value_ids"):
        return "tracking"
    if msg.get("message_type") == "notification" and not msg.get("subtype_id"):
        return "tracking"
    subtype_id = _id_de_m2o(msg.get("subtype_id"))
    if subtype_id in subtipos_cierre:
        return "nota_interna"
    return "comentario"


def _rol_autor(msg: dict, ids_empleados: set[int]) -> str:
    """
    Deriva autor_tipo: empleado | cliente | sistema.

    Odoo no marca el rol explicitamente; lo deducimos: autor que es un usuario
    interno -> empleado; mensaje de sistema (sin autor) -> sistema; resto -> cliente.
    """
    author_id = _id_de_m2o(msg.get("author_id"))
    if author_id is None:
        return "sistema"
    if author_id in ids_empleados:
        return "empleado"
    return "cliente"


_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _email_limpio(valor: str) -> str:
    """
    Extrae la direccion de un `email_from` de Odoo, que viene como cabecera de
    correo ('Nombre Apellido <mail@dominio.com>'). Devuelve solo el email.
    """
    if not valor:
        return ""
    encontrado = _RE_EMAIL.search(str(valor))
    return encontrado.group(0).lower() if encontrado else ""


def _emails_de_partners(odoo: OdooUniversalAPI, partner_ids: set[int]) -> dict[int, str]:
    """
    Devuelve {partner_id: email} para los autores de mensajes. Es la fuente
    fiable del email del autor: `email_from` es solo la cabecera del correo.
    """
    partner_ids = {p for p in partner_ids if p}
    if not partner_ids:
        return {}
    try:
        partners = odoo.execute(
            "res.partner", "read", list(partner_ids), fields=["id", "email"]
        )
    except OdooExecutionError:
        return {}
    return {p["id"]: (p.get("email") or "") for p in partners}


def exportar_historial_jsonl(
    odoo: OdooUniversalAPI,
    limite: int | None = None,
    incluir_tracking: bool = False,
    desde: str | None = None,
    hasta: str | None = None,
    recortar_citas: bool = True,
    solo_abiertos: bool = False,
    etapas_cierre: list[str] | None = None,
) -> str:
    """
    Genera historial.jsonl: un mail.message del chatter por linea (seccion 5).

    incluir_tracking: si False (recomendado por el doc), omite las notificaciones
    automaticas de tracking. Si True, se incluyen marcadas como tipo 'tracking'.
    recortar_citas: recorta el hilo citado y las firmas de los correos (5.3).
    solo_abiertos: acota el historial a los tickets abiertos (seccion 7: una de
    las opciones de alcance acordables es "historial completo solo para abiertos").
    desde/hasta: ventana sobre write_date del ticket (re-exportacion incremental).
    """
    ids_tickets = _ids_tickets(odoo, limite, desde, hasta)
    if solo_abiertos and ids_tickets:
        ids_tickets = _filtrar_abiertos(odoo, ids_tickets, etapas_cierre)
    if not ids_tickets:
        return ""

    modelo = _detectar_modelo_ticket(odoo)

    # Subtipos que son "nota interna" (internal=True) para clasificar.
    try:
        subtipos_internos = odoo.execute(
            "mail.message.subtype", "search", [[["internal", "=", True]]]
        )
    except OdooExecutionError:
        subtipos_internos = []
    subtipos_cierre = set(subtipos_internos)

    try:
        mensajes = odoo.execute(
            "mail.message", "search_read",
            [[["model", "=", modelo], ["res_id", "in", ids_tickets]]],
            fields=[
                "id", "res_id", "date", "message_type", "subtype_id",
                "author_id", "email_from", "body", "tracking_value_ids",
            ],
            order="date",
        )
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudo leer el historial: {e}") from e

    # odoo_ref por ticket: reutilizamos ticket_ref si existe, si no el id.
    campos_ticket = odoo.execute(modelo, "fields_get", [], attributes=["type"])
    campo_ref = "ticket_ref" if "ticket_ref" in campos_ticket else None
    refs = {}
    if campo_ref:
        for t in odoo.execute(modelo, "read", ids_tickets, fields=["id", campo_ref]):
            refs[t["id"]] = t.get(campo_ref) or t["id"]

    # Usuarios internos (para el rol del autor) y emails de autores.
    ids_autores = {_id_de_m2o(m.get("author_id")) for m in mensajes}
    partners_internos = _partners_de_usuarios_internos(odoo, ids_autores)
    emails_autores = _emails_de_partners(odoo, ids_autores)

    lineas = []
    con_imagenes = 0
    for m in mensajes:
        tipo = _clasificar_mensaje(m, subtipos_cierre)
        if tipo == "tracking" and not incluir_tracking:
            continue

        cuerpo_html = m.get("body") or ""
        author_id = _id_de_m2o(m.get("author_id"))
        # El email del partner es la fuente fiable; email_from es la cabecera
        # del correo ('Nombre <mail@x>') y solo sirve de respaldo.
        autor_email = emails_autores.get(author_id) or _email_limpio(m.get("email_from"))

        # Las imagenes embebidas viajan como adjuntos del ZIP (seccion 5.3); en
        # el mensaje dejamos la referencia para que la importacion las relacione.
        embebidas = imagenes_embebidas(cuerpo_html)
        if embebidas["attachment_ids"] or embebidas["inline"]:
            con_imagenes += 1

        registro = {
            "odoo_ref": refs.get(m["res_id"], m["res_id"]),
            "odoo_message_id": m["id"],
            "fecha": fecha_iso(m.get("date")),
            "tipo": tipo,
            "autor_tipo": _rol_autor(
                {"author_id": m.get("author_id")}, partners_internos
            ),
            "autor_nombre": _nombre_de_m2o(m.get("author_id")),
            "autor_email": autor_email,
            "cuerpo": html_a_texto(cuerpo_html, recortar_citas=recortar_citas),
        }
        if embebidas["attachment_ids"]:
            registro["imagenes_embebidas"] = embebidas["attachment_ids"]
        lineas.append(json.dumps(registro, ensure_ascii=False))

    logger.info(
        "HELPDESK_EXPORT | mensajes exportados=%d (con imagenes embebidas=%d)",
        len(lineas), con_imagenes,
    )
    return "\n".join(lineas) + ("\n" if lineas else "")


def _filtrar_abiertos(
    odoo: OdooUniversalAPI,
    ids_tickets: list[int],
    etapas_cierre: list[str] | None,
) -> list[int]:
    """
    Deja solo los tickets en etapa NO de cierre. Sirve para el alcance
    "historial completo solo para abiertos" de la seccion 7.
    """
    modelo = _detectar_modelo_ticket(odoo)
    mapa_cierre = _mapa_etapas_cierre(odoo, etapas_cierre)
    try:
        tickets = odoo.execute(modelo, "read", ids_tickets, fields=["id", "stage_id"])
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudieron leer los tickets: {e}") from e
    return [
        t["id"] for t in tickets
        if not mapa_cierre.get(_id_de_m2o(t.get("stage_id")))
    ]


def _partners_de_usuarios_internos(odoo: OdooUniversalAPI, partner_ids: set[int]) -> set[int]:
    """
    De un conjunto de partner_ids (autores de mensajes), devuelve los que
    corresponden a un usuario interno de Odoo (empleados). El author_id de
    mail.message apunta a res.partner; un empleado tiene un res.users ligado.
    """
    partner_ids = {p for p in partner_ids if p}
    if not partner_ids:
        return set()
    try:
        usuarios = odoo.execute(
            "res.users", "search_read",
            [[["partner_id", "in", list(partner_ids)], ["share", "=", False]]],
            fields=["partner_id"],
        )
    except OdooExecutionError:
        return set()
    return {_id_de_m2o(u.get("partner_id")) for u in usuarios}


# ---------------------------------------------------------------------------
# 3. adjuntos.zip + manifiesto_adjuntos.csv
# ---------------------------------------------------------------------------

# Cuantos binarios se piden a Odoo por llamada. Los `datas` van en base64 (~33%
# mas grandes que el fichero), asi que leerlos todos de golpe revienta la memoria
# en una migracion real: se leen por lotes y se escriben al ZIP segun llegan.
LOTE_ADJUNTOS = 50


def exportar_adjuntos_zip(
    odoo: OdooUniversalAPI,
    limite: int | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    incluir_embebidas: bool = True,
    destino=None,
) -> bytes:
    """
    Genera un ZIP con los adjuntos fisicos de los tickets + manifiesto_adjuntos.csv
    en la raiz del ZIP (seccion 6). Los archivos se organizan en carpetas por
    odoo_ref.

    Los binarios se leen de Odoo por lotes (LOTE_ADJUNTOS) y se escriben al ZIP
    segun llegan: nunca se tiene toda la exportacion en memoria a la vez.

    incluir_embebidas: anade tambien las imagenes embebidas en el cuerpo de los
    mensajes (seccion 5.3), que de otro modo se perderian al apagar Odoo.
    destino: fichero binario donde escribir el ZIP (si se omite, se usa memoria
    y se devuelven los bytes). Para volumenes grandes, pasar un fichero en disco.
    """
    ids_tickets = _ids_tickets(odoo, limite, desde, hasta)
    modelo = _detectar_modelo_ticket(odoo)

    if not ids_tickets:
        return _zip_vacio()

    # Metadatos de los adjuntos SIN el binario: barato y nos da el inventario.
    try:
        adjuntos = odoo.execute(
            "ir.attachment", "search_read",
            [[["res_model", "=", modelo], ["res_id", "in", ids_tickets]]],
            fields=[
                "id", "res_id", "name", "mimetype", "file_size",
                "create_date", "create_uid",
            ],
            order="id",
        )
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudieron leer los adjuntos: {e}") from e

    # Mensajes de estos tickets, para ligar adjuntos a su odoo_message_id y para
    # rescatar las imagenes embebidas en el cuerpo.
    mensajes = odoo.execute(
        "mail.message", "search_read",
        [[["model", "=", modelo], ["res_id", "in", ids_tickets]]],
        fields=["id", "res_id", "attachment_ids", "body"],
    )
    adjunto_a_mensaje = {}
    for msg in mensajes:
        for att_id in msg.get("attachment_ids") or []:
            adjunto_a_mensaje[att_id] = msg["id"]

    if incluir_embebidas:
        adjuntos += _adjuntos_embebidos(
            odoo, mensajes, adjunto_a_mensaje, {a["id"] for a in adjuntos}
        )

    emails_subida = _cache_email_usuarios(
        odoo, {_id_de_m2o(a.get("create_uid")) for a in adjuntos}
    )

    buffer = destino if destino is not None else io.BytesIO()
    manifiesto = io.StringIO()
    writer = csv.DictWriter(
        manifiesto, fieldnames=COLUMNAS_MANIFIESTO, quoting=csv.QUOTE_MINIMAL,
        extrasaction="ignore", lineterminator="\r\n",
    )
    writer.writeheader()

    escritos = 0
    lector = _LectorBinarios(odoo, adjuntos)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        usados: set[str] = set()
        for a in adjuntos:
            odoo_ref = a["res_id"]
            nombre = a.get("name") or f"adjunto_{a['id']}"
            ruta = _ruta_unica(f"{odoo_ref}/{nombre}", usados)

            # datos_b64 ya viene resuelto para las imagenes inline (data: URI);
            # el resto se lee de Odoo por lotes justo antes de escribirlo.
            datas = a.get("datos_b64")
            if datas is None:
                datas = lector.binario(a["id"])
            if datas:
                try:
                    zf.writestr(ruta, base64.b64decode(datas))
                    escritos += 1
                except Exception as e:  # adjunto corrupto: se anota pero no rompe
                    logger.warning("HELPDESK_EXPORT | adjunto %s ilegible: %s", a["id"], e)
                    continue
            else:
                # Sin binario (p.ej. URL externa): se anota en el manifiesto igual.
                logger.info("HELPDESK_EXPORT | adjunto %s sin binario (datas vacio)", a["id"])

            writer.writerow({
                "odoo_ref": odoo_ref,
                "odoo_message_id": adjunto_a_mensaje.get(a["id"], a.get("message_id", "")),
                "odoo_attachment_id": a["id"],
                "ruta_en_zip": ruta,
                "nombre_archivo": nombre,
                "mimetype": a.get("mimetype") or "",
                "size_bytes": a.get("file_size") or "",
                "fecha": fecha_iso(a.get("create_date")),
                "subido_por_email": emails_subida.get(_id_de_m2o(a.get("create_uid")), ""),
            })

        zf.writestr("manifiesto_adjuntos.csv", manifiesto.getvalue())

    logger.info(
        "HELPDESK_EXPORT | adjuntos en manifiesto=%d, binarios escritos=%d",
        len(adjuntos), escritos,
    )
    return buffer.getvalue() if destino is None else b""


class _LectorBinarios:
    """
    Sirve los binarios de ir.attachment leyendolos de Odoo por lotes.

    Mantiene en memoria como mucho un lote (LOTE_ADJUNTOS binarios): cuando se
    pide un id que no esta cacheado, trae el bloque al que pertenece y descarta
    el anterior. Asi el pico de memoria no depende del tamano de la migracion.
    """

    def __init__(self, odoo: OdooUniversalAPI, adjuntos: list[dict]):
        self._odoo = odoo
        # Solo los que hay que pedir a Odoo (los inline ya traen su binario).
        self._pendientes = [a["id"] for a in adjuntos if a.get("datos_b64") is None]
        self._cache: dict[int, str] = {}

    def binario(self, attachment_id: int) -> str:
        if attachment_id not in self._cache:
            self._cargar_lote(attachment_id)
        return self._cache.get(attachment_id, "")

    def _cargar_lote(self, attachment_id: int) -> None:
        try:
            posicion = self._pendientes.index(attachment_id)
        except ValueError:
            posicion = 0
        lote = self._pendientes[posicion:posicion + LOTE_ADJUNTOS]
        if not lote:
            return
        try:
            regs = self._odoo.execute(
                "ir.attachment", "read", lote, fields=["id", "datas"]
            )
        except OdooExecutionError as e:
            logger.warning("HELPDESK_EXPORT | lote de binarios ilegible: %s", e)
            regs = []
        # Se descarta el lote anterior: el pico de memoria queda acotado.
        self._cache = {r["id"]: (r.get("datas") or "") for r in regs}


def _adjuntos_embebidos(
    odoo: OdooUniversalAPI,
    mensajes: list[dict],
    adjunto_a_mensaje: dict,
    ya_incluidos: set[int],
) -> list[dict]:
    """
    Rescata las imagenes embebidas en el cuerpo de los mensajes (seccion 5.3).

    Dos casos:
      - <img src="/web/image/N">: N es un ir.attachment que puede no estar ligado
        al ticket (res_model distinto), asi que se lee aparte y se anade.
      - <img src="data:image/png;base64,...">: el binario esta en el propio HTML;
        se sintetiza una entrada con id negativo (no existe en Odoo).
    """
    ids_web: dict[int, int] = {}       # attachment_id -> message_id
    inline: list[dict] = []
    for msg in mensajes:
        encontrado = imagenes_embebidas(msg.get("body") or "")
        for att_id in encontrado["attachment_ids"]:
            if att_id not in ya_incluidos:
                ids_web.setdefault(att_id, msg["id"])
        for n, img in enumerate(encontrado["inline"], start=1):
            extension = (img["mimetype"].split("/")[-1] or "bin")[:8]
            inline.append({
                # Id sintetico (negativo): estas imagenes no son un ir.attachment.
                "id": -(msg["id"] * 100 + n),
                "res_id": msg["res_id"],
                "message_id": msg["id"],
                "name": f"inline_{msg['id']}_{n}.{extension}",
                "mimetype": img["mimetype"],
                "file_size": len(img["datos_b64"]) * 3 // 4,
                "create_date": False,
                "create_uid": False,
                "datos_b64": img["datos_b64"],
            })

    extra = list(inline)
    if ids_web:
        try:
            regs = odoo.execute(
                "ir.attachment", "read", list(ids_web),
                fields=["id", "name", "mimetype", "file_size", "create_date", "create_uid"],
            )
        except OdooExecutionError as e:
            logger.warning("HELPDESK_EXPORT | imagenes embebidas ilegibles: %s", e)
            regs = []
        for r in regs:
            msg_id = ids_web[r["id"]]
            r["res_id"] = next(
                (m["res_id"] for m in mensajes if m["id"] == msg_id), None
            )
            r["message_id"] = msg_id
            extra.append(r)
            adjunto_a_mensaje.setdefault(r["id"], msg_id)

    if extra:
        logger.info("HELPDESK_EXPORT | imagenes embebidas rescatadas=%d", len(extra))
    return extra


def _ruta_unica(ruta: str, usados: set[str]) -> str:
    """Evita colisiones de nombre dentro del ZIP anadiendo un sufijo numerico."""
    if ruta not in usados:
        usados.add(ruta)
        return ruta
    base, punto, ext = ruta.rpartition(".")
    n = 2
    while True:
        candidato = f"{base}_{n}.{ext}" if punto else f"{ruta}_{n}"
        if candidato not in usados:
            usados.add(candidato)
            return candidato
        n += 1


def _zip_vacio() -> bytes:
    """ZIP con solo el manifiesto (cabecera) cuando no hay tickets/adjuntos."""
    buffer = io.BytesIO()
    manifiesto = io.StringIO()
    csv.DictWriter(
        manifiesto, fieldnames=COLUMNAS_MANIFIESTO, lineterminator="\r\n"
    ).writeheader()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifiesto_adjuntos.csv", manifiesto.getvalue())
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 4. Catalogos (seccion 4)
# ---------------------------------------------------------------------------

def exportar_catalogos(odoo: OdooUniversalAPI) -> dict:
    """
    Devuelve los catalogos previos requeridos antes de importar (seccion 4):
    equipos, etapas por equipo, categorias, etiquetas y usuarios activos.

    Formato: dict de listas, listo para serializar a JSON o a CSVs por catalogo.
    """
    catalogos = {
        "equipos": _catalogo_equipos(odoo),
        "etapas": _catalogo_etapas(odoo),
        "categorias": _catalogo_categorias(odoo),
        "etiquetas": _catalogo_etiquetas(odoo),
        "usuarios": _catalogo_usuarios(odoo),
    }
    logger.info(
        "HELPDESK_EXPORT | catalogos: equipos=%d etapas=%d categorias=%d "
        "etiquetas=%d usuarios=%d",
        len(catalogos["equipos"]), len(catalogos["etapas"]),
        len(catalogos["categorias"]), len(catalogos["etiquetas"]),
        len(catalogos["usuarios"]),
    )
    return catalogos


def _catalogo_equipos(odoo: OdooUniversalAPI) -> list[dict]:
    try:
        equipos = odoo.execute(
            "helpdesk.team", "search_read", [[]],
            fields=["id", "name", "description"], order="name",
        )
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudieron leer los equipos: {e}") from e
    return [
        {"nombre": e.get("name") or "", "descripcion": html_a_texto(e.get("description") or "")}
        for e in equipos
    ]


def _catalogo_etapas(odoo: OdooUniversalAPI) -> list[dict]:
    """
    Etapas de cada equipo con su orden, y si son inicial/cierre. helpdesk.stage
    tiene team_ids (m2m) y sequence; la etapa inicial es la de menor sequence.
    """
    try:
        etapas = odoo.execute(
            "helpdesk.stage", "search_read", [[]],
            fields=["id", "name", "sequence", "fold", "team_ids"], order="sequence",
        )
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudieron leer las etapas: {e}") from e

    # Nombres de equipos para resolver team_ids.
    ids_equipos = {tid for e in etapas for tid in (e.get("team_ids") or [])}
    nombres_equipos = _leer_nombres(odoo, "helpdesk.team", ids_equipos)

    # La etapa inicial de cada equipo = la de menor sequence entre sus etapas.
    min_seq_por_equipo: dict[int, int] = {}
    for e in etapas:
        for tid in (e.get("team_ids") or []):
            seq = e.get("sequence") or 0
            if tid not in min_seq_por_equipo or seq < min_seq_por_equipo[tid]:
                min_seq_por_equipo[tid] = seq

    filas = []
    for e in etapas:
        equipos_de_etapa = e.get("team_ids") or [None]
        for tid in equipos_de_etapa:
            filas.append({
                "equipo": nombres_equipos.get(tid, "") if tid else "(todas)",
                "etapa": e.get("name") or "",
                "orden": e.get("sequence") or 0,
                "es_inicial": bool(tid and e.get("sequence", 0) == min_seq_por_equipo.get(tid)),
                "es_cierre": bool(e.get("fold")),
            })
    return filas


def _catalogo_categorias(odoo: OdooUniversalAPI) -> list[dict]:
    """Categorias/tipos de ticket, si el modelo existe en esta instalacion."""
    try:
        existe = odoo.execute(
            "ir.model", "search_count", [[["model", "=", "helpdesk.ticket.type"]]]
        )
    except OdooExecutionError:
        existe = 0
    if not existe:
        return []
    try:
        tipos = odoo.execute(
            "helpdesk.ticket.type", "search_read", [[]], fields=["name"], order="name"
        )
    except OdooExecutionError:
        return []
    return [{"nombre": t.get("name") or ""} for t in tipos]


def _catalogo_etiquetas(odoo: OdooUniversalAPI) -> list[dict]:
    try:
        tags = odoo.execute(
            "helpdesk.tag", "search_read", [[]], fields=["name"], order="name"
        )
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudieron leer las etiquetas: {e}") from e
    return [{"nombre": t.get("name") or ""} for t in tags]


def _catalogo_usuarios(odoo: OdooUniversalAPI) -> list[dict]:
    """
    Usuarios activos internos (nombre + email). El email debe coincidir con el
    del usuario en la plataforma SESTIA (seccion 4).
    """
    try:
        usuarios = odoo.execute(
            "res.users", "search_read",
            [[["active", "=", True], ["share", "=", False]]],
            fields=["name", "login", "email"], order="name",
        )
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudieron leer los usuarios: {e}") from e
    return [
        {"nombre": u.get("name") or "", "email": u.get("email") or u.get("login") or ""}
        for u in usuarios
    ]


# ---------------------------------------------------------------------------
# 5. Volumenes (pasos 2 y 7 del plan; checklist de la seccion 7)
# ---------------------------------------------------------------------------

def contar_volumenes(
    odoo: OdooUniversalAPI,
    desde: str | None = None,
    hasta: str | None = None,
) -> dict:
    """
    Conteos previos a la exportacion: numero de tickets (total, abiertos,
    cerrados), numero de mensajes por tipo, y numero y peso total de adjuntos.

    Es el dato que el plan pide entregar ANTES de exportar (paso 2) para decidir
    el alcance del historial y si el ZIP hay que trocearlo (seccion 6).
    """
    modelo = _detectar_modelo_ticket(odoo)
    dominio = _dominio_tickets(desde, hasta)

    try:
        ids_tickets = odoo.execute(modelo, "search", [dominio], order="id")
    except OdooExecutionError as e:
        raise HelpdeskExportError(f"No se pudieron contar los tickets: {e}") from e

    abiertos = len(_filtrar_abiertos(odoo, ids_tickets, None)) if ids_tickets else 0

    mensajes = {"total": 0, "comentario": 0, "nota_interna": 0, "tracking": 0}
    adjuntos = {"total": 0, "bytes": 0, "sin_binario": 0}

    if ids_tickets:
        try:
            subtipos_internos = set(odoo.execute(
                "mail.message.subtype", "search", [[["internal", "=", True]]]
            ))
        except OdooExecutionError:
            subtipos_internos = set()

        msgs = odoo.execute(
            "mail.message", "search_read",
            [[["model", "=", modelo], ["res_id", "in", ids_tickets]]],
            fields=["id", "message_type", "subtype_id", "tracking_value_ids"],
        )
        mensajes["total"] = len(msgs)
        for m in msgs:
            mensajes[_clasificar_mensaje(m, subtipos_internos)] += 1

        atts = odoo.execute(
            "ir.attachment", "search_read",
            [[["res_model", "=", modelo], ["res_id", "in", ids_tickets]]],
            fields=["id", "file_size"],
        )
        adjuntos["total"] = len(atts)
        adjuntos["bytes"] = sum(int(a.get("file_size") or 0) for a in atts)
        adjuntos["sin_binario"] = sum(1 for a in atts if not a.get("file_size"))

    resumen = {
        "ventana": {"desde": desde or "", "hasta": hasta or ""},
        "tickets": {
            "total": len(ids_tickets),
            "abiertos": abiertos,
            "cerrados": len(ids_tickets) - abiertos,
        },
        "mensajes": mensajes,
        "adjuntos": {
            **adjuntos,
            "megabytes": round(adjuntos["bytes"] / (1024 * 1024), 2),
        },
    }
    logger.info(
        "HELPDESK_EXPORT | volumenes: tickets=%d mensajes=%d adjuntos=%d (%.2f MB)",
        resumen["tickets"]["total"], mensajes["total"],
        adjuntos["total"], resumen["adjuntos"]["megabytes"],
    )
    return resumen


def catalogos_a_zip(catalogos: dict) -> bytes:
    """Empaqueta los catalogos como un ZIP con un CSV por catalogo."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for nombre, filas in catalogos.items():
            csv_buffer = io.StringIO()
            if filas:
                writer = csv.DictWriter(
                    csv_buffer, fieldnames=list(filas[0].keys()),
                    quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n",
                )
                writer.writeheader()
                writer.writerows(filas)
            zf.writestr(f"{nombre}.csv", csv_buffer.getvalue())
    return buffer.getvalue()
