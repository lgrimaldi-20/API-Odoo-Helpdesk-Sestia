"""
Tests de la exportacion de Helpdesk (Odoo -> SESTIA).

Dos niveles:
  - unidad: se mockea el conector Odoo (un fake execute que responde por modelo)
    y se comprueba la forma exacta de tickets.csv, historial.jsonl, adjuntos.zip
    y catalogos, incluyendo el mapeo estado open/closed via `fold`.
  - HTTP: se prueba el router /helpdesk/export/* con la logica core parcheada,
    igual que test_routers.py (parchea _login antes de importar api.py).
"""

import base64
import csv
import io
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from core import helpdesk_export as hx
from core.helpdesk_export import (
    HelpdeskExportError,
    exportar_adjuntos_zip,
    exportar_catalogos,
    exportar_historial_jsonl,
    exportar_tickets_csv,
    fecha_iso,
    html_a_texto,
)


# ---------------------------------------------------------------------------
# Fake del conector Odoo
# ---------------------------------------------------------------------------

class FakeOdoo:
    """
    Conector Odoo simulado: responde a execute(model, method, *args, **kwargs)
    en funcion de datos en memoria. Solo implementa lo que la exportacion usa.
    """

    def __init__(self, datos: dict):
        self.datos = datos  # {modelo: [registros...]}
        self.fields = datos.get("_fields", {})

    def execute(self, model, method, *args, **kwargs):
        if model == "ir.model" and method == "search_count":
            # args[0] es el dominio, p.ej. [["model", "=", "X"]].
            objetivo = _valor_condicion(args[0], "model")
            return 1 if objetivo in self.datos else 0

        registros = self.datos.get(model, [])

        if method == "fields_get":
            return self.fields.get(model, {})

        if method == "search":
            dominio = _normalizar_dominio(args[0]) if args else []
            seleccion = _filtrar(registros, dominio)
            limit = kwargs.get("limit") or 0
            ids = [r["id"] for r in seleccion]
            return ids[:limit] if limit else ids

        if method == "search_count":
            return len(registros)

        if method in ("search_read", "read"):
            fields = kwargs.get("fields")
            if method == "read":
                ids = args[0]
                seleccion = [r for r in registros if r["id"] in ids]
            else:
                dominio = _normalizar_dominio(args[0]) if args else []
                seleccion = _filtrar(registros, dominio)
                limit = kwargs.get("limit") or 0
                if limit:
                    seleccion = seleccion[:limit]
            if fields:
                return [{k: r.get(k) for k in fields if k in r} for r in seleccion]
            return seleccion

        raise AssertionError(f"metodo no simulado: {model}.{method}")


def _normalizar_dominio(dominio):
    """
    El core pasa el dominio como primer arg posicional: unas veces envuelto en
    una lista extra ([[cond, cond]]) y otras directo ([cond, cond]). Desenvuelve
    hasta quedarse con la lista de condiciones (cada una es [campo, op, valor]).
    """
    if (
        isinstance(dominio, list) and len(dominio) == 1
        and isinstance(dominio[0], list)
        and (not dominio[0] or isinstance(dominio[0][0], (list, tuple)))
    ):
        return dominio[0]
    return dominio


def _valor_condicion(dominio, campo):
    """Devuelve el valor buscado para un campo dentro de un dominio simple."""
    for cond in _normalizar_dominio(dominio):
        if isinstance(cond, (list, tuple)) and len(cond) == 3 and cond[0] == campo:
            return cond[2]
    return None


def _valor_campo(registro, campo):
    """Valor de un campo para filtrar; de un m2o [id, name] devuelve el id."""
    v = registro.get(campo)
    if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], int):
        return v[0]
    return v


def _filtrar(registros, dominio):
    """Filtro minimo: soporta '=', 'in' y los comparadores '>=' / '<='."""
    out = registros
    for cond in dominio:
        if not isinstance(cond, (list, tuple)) or len(cond) != 3:
            continue
        campo, op, valor = cond
        if op == "=":
            out = [r for r in out if _valor_campo(r, campo) == valor]
        elif op == "in":
            out = [r for r in out if _valor_campo(r, campo) in valor]
        elif op == ">=":
            out = [r for r in out if str(_valor_campo(r, campo) or "") >= valor]
        elif op == "<=":
            out = [r for r in out if str(_valor_campo(r, campo) or "") <= valor]
    return out


@pytest.fixture()
def odoo_helpdesk():
    """Instancia Odoo simulada con dos tickets, mensajes, adjuntos y catalogos."""
    datos = {
        "_fields": {
            "helpdesk.ticket": {
                "ticket_ref": {"type": "char"},
                "partner_email": {"type": "char"},
                "partner_phone": {"type": "char"},
                "category_id": {"type": "many2one"},
                "subcategory_id": {"type": "many2one"},
                "sla_deadline": {"type": "datetime"},
                # Campo personalizado del cliente (seccion 3) y uno relacional,
                # que NO debe salir como columna del CSV.
                "x_studio_origen": {"type": "char"},
                "x_studio_responsable_id": {"type": "many2one"},
            }
        },
        "helpdesk.ticket": [
            {
                "id": 1, "ticket_ref": "HT-0001", "name": "No arranca",
                "description": "<p>El equipo <b>no</b> enciende</p>",
                "team_id": [10, "Soporte"], "stage_id": [100, "Nuevo"],
                "priority": "2", "tag_ids": [1000, 1001],
                "user_id": [50, "Ana"], "create_uid": [50, "Ana"],
                "create_date": "2024-03-15 10:30:00", "close_date": False,
                "write_date": "2024-03-20 08:00:00",
                "partner_email": "cliente@x.com", "partner_phone": "555",
                "partner_name": "Cliente X", "sla_deadline": "2024-03-16 10:30:00",
                "category_id": [7, "Hardware"],
                "subcategory_id": [70, "Portatil"],
                "x_studio_origen": "telefono",
                "x_studio_responsable_id": [50, "Ana"],
            },
            {
                "id": 2, "ticket_ref": "HT-0002", "name": "Duda facturacion",
                "description": "texto plano", "team_id": [10, "Soporte"],
                "stage_id": [103, "Resuelto"], "priority": "0", "tag_ids": [],
                "user_id": False, "create_uid": [50, "Ana"],
                "create_date": "2024-02-01 09:00:00",
                "close_date": "2024-02-02 12:00:00",
                "write_date": "2024-02-02 12:00:00",
                "partner_email": False, "partner_phone": False,
                "partner_name": False, "sla_deadline": False, "category_id": False,
                "subcategory_id": False, "x_studio_origen": False,
                "x_studio_responsable_id": False,
            },
        ],
        "helpdesk.stage": [
            {"id": 100, "name": "Nuevo", "sequence": 1, "fold": False, "team_ids": [10]},
            {"id": 103, "name": "Resuelto", "sequence": 9, "fold": True, "team_ids": [10]},
        ],
        "helpdesk.team": [
            {"id": 10, "name": "Soporte", "description": "Equipo de soporte"},
        ],
        "helpdesk.tag": [
            {"id": 1000, "name": "urgente"},
            {"id": 1001, "name": "vip"},
        ],
        "helpdesk.ticket.type": [
            {"id": 7, "name": "Hardware"},
        ],
        "res.users": [
            {"id": 50, "name": "Ana", "login": "ana@empresa.com",
             "email": "ana@empresa.com", "partner_id": [500, "Ana"],
             "active": True, "share": False},
        ],
        "res.partner": [
            {"id": 500, "name": "Ana", "email": "ana@empresa.com"},
            {"id": 600, "name": "Cliente X", "email": "cliente@x.com"},
        ],
        "mail.message.subtype": [
            {"id": 1, "internal": False},  # comentario
            {"id": 2, "internal": True},   # nota interna
        ],
        "mail.message": [
            {
                "id": 9001, "model": "helpdesk.ticket", "res_id": 1,
                "date": "2024-03-15 11:00:00",
                "message_type": "comment", "subtype_id": [1, "Comentario"],
                "author_id": [500, "Ana"], "email_from": "ana@empresa.com",
                "body": "<p>Revisando el caso</p>", "tracking_value_ids": [],
                "attachment_ids": [7000],
            },
            {
                "id": 9002, "model": "helpdesk.ticket", "res_id": 1,
                "date": "2024-03-15 12:00:00",
                "message_type": "comment", "subtype_id": [2, "Nota"],
                "author_id": [500, "Ana"], "email_from": "ana@empresa.com",
                "body": "Nota interna", "tracking_value_ids": [], "attachment_ids": [],
            },
            {
                "id": 9003, "model": "helpdesk.ticket", "res_id": 1,
                "date": "2024-03-15 13:00:00",
                "message_type": "notification", "subtype_id": False,
                "author_id": False, "email_from": False,
                "body": "Etapa cambiada", "tracking_value_ids": [1], "attachment_ids": [],
            },
            {
                # Correo del cliente: hilo citado + firma + imagen embebida por
                # /web/image (adjunto 7001, NO ligado al ticket) e inline base64.
                "id": 9004, "model": "helpdesk.ticket", "res_id": 2,
                "date": "2024-02-01 10:00:00",
                "message_type": "email", "subtype_id": [1, "Comentario"],
                "author_id": [600, "Cliente X"],
                "email_from": "Cliente X <cliente@x.com>",
                "body": (
                    "<p>Cuerpo nuevo del correo</p>"
                    '<img src="/web/image/7001">'
                    '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">'
                    '<blockquote>El dia 1 escribiste: mensaje anterior</blockquote>'
                    '<div class="gmail_signature">Un saludo, Cliente</div>'
                ),
                "tracking_value_ids": [], "attachment_ids": [],
            },
        ],
        "ir.attachment": [
            {
                "id": 7000, "res_model": "helpdesk.ticket", "res_id": 1,
                "name": "foto.png", "mimetype": "image/png",
                "file_size": 1234, "create_date": "2024-03-15 11:00:00",
                "create_uid": [50, "Ana"], "datas": base64.b64encode(b"PNGDATA").decode(),
            },
            {
                # Embebido en el cuerpo del mensaje 9004: res_model es mail.message,
                # asi que NO lo encuentra la busqueda por ticket.
                "id": 7001, "res_model": "mail.message", "res_id": 9004,
                "name": "captura.png", "mimetype": "image/png",
                "file_size": 99, "create_date": "2024-02-01 10:00:00",
                "create_uid": [50, "Ana"], "datas": base64.b64encode(b"EMBEBIDA").decode(),
            },
        ],
    }
    return FakeOdoo(datos)


# ---------------------------------------------------------------------------
# Utilidades puras
# ---------------------------------------------------------------------------

class TestUtilidades:
    def test_html_a_texto_quita_etiquetas(self):
        assert html_a_texto("<p>Hola <b>mundo</b></p>") == "Hola mundo"

    def test_html_a_texto_vacio(self):
        assert html_a_texto("") == ""
        assert html_a_texto(None) == ""

    def test_fecha_iso_anade_offset(self):
        assert fecha_iso("2024-03-15 10:30:00") == "2024-03-15T10:30:00Z"

    def test_fecha_iso_vacio(self):
        assert fecha_iso(False) == ""
        assert fecha_iso("") == ""

    def test_fecha_iso_respeta_offset_existente(self):
        assert fecha_iso("2024-03-15T10:30:00-04:00") == "2024-03-15T10:30:00-04:00"


# ---------------------------------------------------------------------------
# tickets.csv
# ---------------------------------------------------------------------------

class TestTicketsCsv:
    def test_columnas_y_conteo(self, odoo_helpdesk):
        contenido = exportar_tickets_csv(odoo_helpdesk)
        filas = list(csv.DictReader(io.StringIO(contenido)))
        assert len(filas) == 2
        # Las columnas de la spec van primero; detras, los campos personalizados
        # del cliente (ver TestCamposExtra).
        assert list(filas[0].keys())[:len(hx.COLUMNAS_TICKETS)] == hx.COLUMNAS_TICKETS

    def test_estado_derivado_de_fold(self, odoo_helpdesk):
        contenido = exportar_tickets_csv(odoo_helpdesk)
        filas = {f["odoo_ref"]: f for f in csv.DictReader(io.StringIO(contenido))}
        # Etapa "Nuevo" (fold=False) -> open ; "Resuelto" (fold=True) -> closed.
        assert filas["HT-0001"]["estado"] == "open"
        assert filas["HT-0002"]["estado"] == "closed"

    def test_etiquetas_y_emails(self, odoo_helpdesk):
        filas = list(csv.DictReader(io.StringIO(exportar_tickets_csv(odoo_helpdesk))))
        primera = filas[0]
        assert primera["etiquetas"] == "urgente;vip"
        assert primera["asignado_email"] == "ana@empresa.com"
        assert primera["creado_por_email"] == "ana@empresa.com"
        assert primera["descripcion"] == "El equipo no enciende"
        assert primera["fecha_creacion"] == "2024-03-15T10:30:00Z"

    def test_override_etapas_cierre_por_nombre(self, odoo_helpdesk):
        # Forzamos "Nuevo" como etapa de cierre -> HT-0001 pasa a closed.
        contenido = exportar_tickets_csv(odoo_helpdesk, etapas_cierre=["Nuevo"])
        filas = {f["odoo_ref"]: f for f in csv.DictReader(io.StringIO(contenido))}
        assert filas["HT-0001"]["estado"] == "closed"
        assert filas["HT-0002"]["estado"] == "open"

    def test_limite_de_muestra(self, odoo_helpdesk):
        filas = list(csv.DictReader(io.StringIO(exportar_tickets_csv(odoo_helpdesk, limite=1))))
        assert len(filas) == 1

    def test_error_si_no_hay_helpdesk(self):
        odoo = FakeOdoo({})  # sin helpdesk.ticket registrado
        with pytest.raises(HelpdeskExportError):
            exportar_tickets_csv(odoo)


# ---------------------------------------------------------------------------
# historial.jsonl
# ---------------------------------------------------------------------------

class TestHistorialJsonl:
    def test_excluye_tracking_por_defecto(self, odoo_helpdesk):
        contenido = exportar_historial_jsonl(odoo_helpdesk)
        lineas = [json.loads(l) for l in contenido.splitlines()]
        tipos = [l["tipo"] for l in lineas]
        assert "tracking" not in tipos
        # 9001 comentario, 9002 nota interna, 9004 correo del cliente.
        assert tipos == ["comentario", "nota_interna", "comentario"]

    def test_incluye_tracking_si_se_pide(self, odoo_helpdesk):
        contenido = exportar_historial_jsonl(odoo_helpdesk, incluir_tracking=True)
        tipos = [json.loads(l)["tipo"] for l in contenido.splitlines()]
        assert tipos.count("tracking") == 1

    def test_campos_del_mensaje(self, odoo_helpdesk):
        primera = json.loads(exportar_historial_jsonl(odoo_helpdesk).splitlines()[0])
        assert primera["odoo_ref"] == "HT-0001"
        assert primera["odoo_message_id"] == 9001
        assert primera["autor_tipo"] == "empleado"  # Ana es usuario interno
        assert primera["cuerpo"] == "Revisando el caso"
        assert primera["fecha"] == "2024-03-15T11:00:00Z"

    def test_autor_sistema_en_tracking(self, odoo_helpdesk):
        lineas = [
            json.loads(l)
            for l in exportar_historial_jsonl(odoo_helpdesk, incluir_tracking=True).splitlines()
        ]
        tracking = next(l for l in lineas if l["tipo"] == "tracking")
        assert tracking["autor_tipo"] == "sistema"


# ---------------------------------------------------------------------------
# adjuntos.zip
# ---------------------------------------------------------------------------

class TestAdjuntosZip:
    def test_zip_contiene_manifiesto_y_binario(self, odoo_helpdesk):
        contenido = exportar_adjuntos_zip(odoo_helpdesk)
        zf = zipfile.ZipFile(io.BytesIO(contenido))
        nombres = zf.namelist()
        assert "manifiesto_adjuntos.csv" in nombres
        # La carpeta lleva el odoo_ref, el mismo que usa tickets.csv.
        assert "HT-0001/foto.png" in nombres
        assert zf.read("HT-0001/foto.png") == b"PNGDATA"

    def test_manifiesto_liga_adjunto_a_mensaje(self, odoo_helpdesk):
        zf = zipfile.ZipFile(io.BytesIO(exportar_adjuntos_zip(odoo_helpdesk)))
        manifiesto = list(csv.DictReader(io.StringIO(zf.read("manifiesto_adjuntos.csv").decode())))
        fila = manifiesto[0]
        assert fila["odoo_attachment_id"] == "7000"
        assert fila["odoo_message_id"] == "9001"  # adjunto ligado al mensaje
        assert fila["mimetype"] == "image/png"
        assert fila["ruta_en_zip"] == "HT-0001/foto.png"


# ---------------------------------------------------------------------------
# Campos personalizados y subcategoria (seccion 3)
# ---------------------------------------------------------------------------

class TestCamposExtra:
    def test_subcategoria_se_resuelve_por_nombre(self, odoo_helpdesk):
        filas = {f["odoo_ref"]: f for f in csv.DictReader(
            io.StringIO(exportar_tickets_csv(odoo_helpdesk)))}
        assert filas["HT-0001"]["subcategoria"] == "Portatil"
        assert filas["HT-0002"]["subcategoria"] == ""

    def test_campos_personalizados_al_final_de_la_cabecera(self, odoo_helpdesk):
        contenido = exportar_tickets_csv(odoo_helpdesk)
        columnas = list(csv.DictReader(io.StringIO(contenido)).fieldnames)
        # Las 18 columnas de la spec, en orden, y luego los x_ del cliente.
        assert columnas[:len(hx.COLUMNAS_TICKETS)] == hx.COLUMNAS_TICKETS
        assert columnas[len(hx.COLUMNAS_TICKETS):] == ["x_studio_origen"]

    def test_personalizado_relacional_se_excluye(self, odoo_helpdesk):
        columnas = csv.DictReader(
            io.StringIO(exportar_tickets_csv(odoo_helpdesk))).fieldnames
        # many2one no cabe en una celda: no se exporta como columna.
        assert "x_studio_responsable_id" not in columnas

    def test_valor_del_campo_personalizado(self, odoo_helpdesk):
        filas = {f["odoo_ref"]: f for f in csv.DictReader(
            io.StringIO(exportar_tickets_csv(odoo_helpdesk)))}
        assert filas["HT-0001"]["x_studio_origen"] == "telefono"
        assert filas["HT-0002"]["x_studio_origen"] == ""  # False -> vacio

    def test_categoria_desde_ticket_type_id_estandar(self, odoo_helpdesk):
        # El Helpdesk estandar de Odoo llama al campo `ticket_type_id`, no
        # `category_id`: con el nombre estandar la categoria debe salir igual.
        odoo_helpdesk.fields["helpdesk.ticket"].pop("category_id")
        odoo_helpdesk.fields["helpdesk.ticket"]["ticket_type_id"] = {"type": "many2one"}
        for t in odoo_helpdesk.datos["helpdesk.ticket"]:
            t["ticket_type_id"] = t.pop("category_id")

        filas = {f["odoo_ref"]: f for f in csv.DictReader(
            io.StringIO(exportar_tickets_csv(odoo_helpdesk)))}
        assert filas["HT-0001"]["categoria"] == "Hardware"

    def test_categoria_vacia_si_no_existe_el_campo(self, odoo_helpdesk):
        odoo_helpdesk.fields["helpdesk.ticket"].pop("category_id")
        for t in odoo_helpdesk.datos["helpdesk.ticket"]:
            t.pop("category_id", None)
        filas = list(csv.DictReader(io.StringIO(exportar_tickets_csv(odoo_helpdesk))))
        assert all(f["categoria"] == "" for f in filas)

    def test_avisa_de_cerrado_sin_fecha_cierre(self, odoo_helpdesk, caplog):
        # HT-0002 esta cerrado; le quitamos close_date para provocar el aviso.
        odoo_helpdesk.datos["helpdesk.ticket"][1]["close_date"] = False
        with caplog.at_level("WARNING", logger="api-odoo"):
            exportar_tickets_csv(odoo_helpdesk)
        assert "cerrados sin fecha_cierre" in caplog.text


# ---------------------------------------------------------------------------
# Ventana de fechas (paso 8: corte y re-exportacion incremental)
# ---------------------------------------------------------------------------

class TestVentanaDeFechas:
    def test_desde_acota_por_write_date(self, odoo_helpdesk):
        # HT-0002 se modifico el 2024-02-02; HT-0001 el 2024-03-20.
        contenido = exportar_tickets_csv(odoo_helpdesk, desde="2024-03-01")
        refs = [f["odoo_ref"] for f in csv.DictReader(io.StringIO(contenido))]
        assert refs == ["HT-0001"]

    def test_hasta_acota_por_write_date(self, odoo_helpdesk):
        contenido = exportar_tickets_csv(odoo_helpdesk, hasta="2024-03-01")
        refs = [f["odoo_ref"] for f in csv.DictReader(io.StringIO(contenido))]
        assert refs == ["HT-0002"]

    def test_historial_respeta_la_ventana(self, odoo_helpdesk):
        contenido = exportar_historial_jsonl(odoo_helpdesk, desde="2024-03-01")
        refs = {json.loads(l)["odoo_ref"] for l in contenido.splitlines()}
        assert refs == {"HT-0001"}  # los mensajes de HT-0002 quedan fuera

    def test_acepta_iso_con_offset(self):
        assert hx._a_fecha_odoo("2024-03-15T10:30:00-04:00") == "2024-03-15 10:30:00"
        assert hx._a_fecha_odoo("2024-03-15T10:30:00Z") == "2024-03-15 10:30:00"
        assert hx._a_fecha_odoo("2024-03-15") == "2024-03-15 00:00:00"


# ---------------------------------------------------------------------------
# Cuerpo del mensaje: citas, firmas e imagenes embebidas (seccion 5.3)
# ---------------------------------------------------------------------------

class TestCuerpoDeMensaje:
    def test_recorta_hilo_citado_y_firma(self):
        html = (
            "<p>Cuerpo nuevo</p>"
            "<blockquote>mensaje anterior</blockquote>"
            '<div class="gmail_signature">Un saludo</div>'
        )
        assert html_a_texto(html, recortar_citas=True) == "Cuerpo nuevo"

    def test_sin_recorte_conserva_todo(self):
        html = "<p>Cuerpo nuevo</p><blockquote>mensaje anterior</blockquote>"
        texto = html_a_texto(html, recortar_citas=False)
        assert "mensaje anterior" in texto

    def test_historial_recorta_por_defecto(self, odoo_helpdesk):
        lineas = [json.loads(l) for l in
                  exportar_historial_jsonl(odoo_helpdesk).splitlines()]
        correo = next(l for l in lineas if l["odoo_message_id"] == 9004)
        assert correo["cuerpo"] == "Cuerpo nuevo del correo"

    def test_detecta_imagenes_embebidas(self):
        html = ('<img src="/web/image/7001">'
                '<img src="data:image/gif;base64,R0lGODlh">')
        encontrado = hx.imagenes_embebidas(html)
        assert encontrado["attachment_ids"] == [7001]
        assert encontrado["inline"][0]["mimetype"] == "image/gif"

    def test_mensaje_referencia_sus_imagenes(self, odoo_helpdesk):
        lineas = [json.loads(l) for l in
                  exportar_historial_jsonl(odoo_helpdesk).splitlines()]
        correo = next(l for l in lineas if l["odoo_message_id"] == 9004)
        assert correo["imagenes_embebidas"] == [7001]

    def test_autor_email_limpio_de_la_cabecera(self, odoo_helpdesk):
        lineas = [json.loads(l) for l in
                  exportar_historial_jsonl(odoo_helpdesk).splitlines()]
        correo = next(l for l in lineas if l["odoo_message_id"] == 9004)
        # 'Cliente X <cliente@x.com>' -> solo el email; ademas es rol cliente.
        assert correo["autor_email"] == "cliente@x.com"
        assert correo["autor_tipo"] == "cliente"

    def test_descripcion_tambien_recorta_citas(self, odoo_helpdesk):
        # La descripcion suele ser el correo que abrio el ticket, con hilo citado.
        odoo_helpdesk.datos["helpdesk.ticket"][0]["description"] = (
            "<p>Problema original</p>"
            "<blockquote>correo anterior citado</blockquote>"
        )
        filas = {f["odoo_ref"]: f for f in csv.DictReader(
            io.StringIO(exportar_tickets_csv(odoo_helpdesk)))}
        assert filas["HT-0001"]["descripcion"] == "Problema original"

    def test_descripcion_sin_recorte_si_se_pide(self, odoo_helpdesk):
        odoo_helpdesk.datos["helpdesk.ticket"][0]["description"] = (
            "<p>Problema original</p><blockquote>correo anterior</blockquote>"
        )
        filas = {f["odoo_ref"]: f for f in csv.DictReader(io.StringIO(
            exportar_tickets_csv(odoo_helpdesk, recortar_citas=False)))}
        assert "correo anterior" in filas["HT-0001"]["descripcion"]

    def test_solo_abiertos_acota_el_historial(self, odoo_helpdesk):
        # HT-0002 esta en etapa "Resuelto" (fold=True) -> se excluye.
        contenido = exportar_historial_jsonl(odoo_helpdesk, solo_abiertos=True)
        refs = {json.loads(l)["odoo_ref"] for l in contenido.splitlines()}
        assert refs == {"HT-0001"}


# ---------------------------------------------------------------------------
# Adjuntos: imagenes embebidas rescatadas y lectura por lotes
# ---------------------------------------------------------------------------

class TestAdjuntosEmbebidos:
    def test_rescata_la_imagen_embebida_del_cuerpo(self, odoo_helpdesk):
        zf = zipfile.ZipFile(io.BytesIO(exportar_adjuntos_zip(odoo_helpdesk)))
        manifiesto = list(csv.DictReader(
            io.StringIO(zf.read("manifiesto_adjuntos.csv").decode())))
        ids = {f["odoo_attachment_id"] for f in manifiesto}
        # 7001 solo aparece embebido en el cuerpo del mensaje 9004.
        assert "7001" in ids
        fila = next(f for f in manifiesto if f["odoo_attachment_id"] == "7001")
        assert zf.read(fila["ruta_en_zip"]) == b"EMBEBIDA"
        assert fila["odoo_message_id"] == "9004"

    def test_rescata_la_imagen_inline_base64(self, odoo_helpdesk):
        zf = zipfile.ZipFile(io.BytesIO(exportar_adjuntos_zip(odoo_helpdesk)))
        nombres = zf.namelist()
        # La data: URI se materializa como fichero inline_<mensaje>_<n>.<ext>.
        assert any(n.endswith("inline_9004_1.gif") for n in nombres)

    def test_se_pueden_omitir_las_embebidas(self, odoo_helpdesk):
        zf = zipfile.ZipFile(io.BytesIO(
            exportar_adjuntos_zip(odoo_helpdesk, incluir_embebidas=False)))
        manifiesto = list(csv.DictReader(
            io.StringIO(zf.read("manifiesto_adjuntos.csv").decode())))
        assert {f["odoo_attachment_id"] for f in manifiesto} == {"7000"}

    def test_binarios_se_leen_por_lotes(self, odoo_helpdesk, monkeypatch):
        # Con un lote de 1, cada binario se pide por separado: comprobamos que
        # la lectura sigue siendo correcta y que no se piden todos de golpe.
        monkeypatch.setattr(hx, "LOTE_ADJUNTOS", 1)
        lecturas = []
        original = odoo_helpdesk.execute

        def espiar(model, method, *args, **kwargs):
            if model == "ir.attachment" and "datas" in (kwargs.get("fields") or []):
                lecturas.append(list(args[0]))
            return original(model, method, *args, **kwargs)

        monkeypatch.setattr(odoo_helpdesk, "execute", espiar)
        zf = zipfile.ZipFile(io.BytesIO(exportar_adjuntos_zip(odoo_helpdesk)))
        assert zf.read("HT-0001/foto.png") == b"PNGDATA"
        assert all(len(l) == 1 for l in lecturas)


# ---------------------------------------------------------------------------
# Integridad referencial: odoo_ref enlaza los tres archivos entre si
# ---------------------------------------------------------------------------

class TestOdooRefConsistente:
    """
    El odoo_ref es lo unico que enlaza tickets.csv, historial.jsonl y el
    manifiesto de adjuntos. Si un archivo usara el id numerico y otro el numero
    visible, la importacion no podria relacionarlos.
    """

    def _refs(self, odoo):
        tickets = {f["odoo_ref"] for f in csv.DictReader(
            io.StringIO(exportar_tickets_csv(odoo)))}
        hist = {str(json.loads(l)["odoo_ref"])
                for l in exportar_historial_jsonl(odoo).splitlines()}
        zf = zipfile.ZipFile(io.BytesIO(exportar_adjuntos_zip(odoo)))
        man = {f["odoo_ref"] for f in csv.DictReader(
            io.StringIO(zf.read("manifiesto_adjuntos.csv").decode()))}
        return tickets, hist, man

    def test_los_tres_archivos_usan_la_misma_referencia(self, odoo_helpdesk):
        tickets, hist, man = self._refs(odoo_helpdesk)
        assert hist <= tickets, f"historial huerfano: {hist - tickets}"
        assert man <= tickets, f"adjuntos huerfanos: {man - tickets}"

    def test_usa_el_numero_visible_cuando_existe(self, odoo_helpdesk):
        tickets, _, man = self._refs(odoo_helpdesk)
        assert tickets == {"HT-0001", "HT-0002"}
        assert man <= {"HT-0001", "HT-0002"}

    def test_cae_al_id_si_no_hay_ticket_ref(self, odoo_helpdesk):
        # Instalacion sin el campo ticket_ref: los tres deben usar el id.
        odoo_helpdesk.fields["helpdesk.ticket"].pop("ticket_ref")
        tickets, hist, man = self._refs(odoo_helpdesk)
        assert tickets == {"1", "2"}
        assert hist <= tickets
        assert man <= tickets

    def test_ref_con_barras_no_rompe_la_ruta_del_zip(self, odoo_helpdesk):
        # Un numero tipo 'SOP/2024/001' creaba subcarpetas dentro del ZIP.
        odoo_helpdesk.datos["helpdesk.ticket"][0]["ticket_ref"] = "SOP/2024/001"
        zf = zipfile.ZipFile(io.BytesIO(exportar_adjuntos_zip(odoo_helpdesk)))
        rutas = [n for n in zf.namelist() if n != "manifiesto_adjuntos.csv"]
        assert all(r.count("/") == 1 for r in rutas), rutas
        # El manifiesto conserva la referencia real, sin sanear.
        man = list(csv.DictReader(io.StringIO(
            zf.read("manifiesto_adjuntos.csv").decode())))
        assert any(f["odoo_ref"] == "SOP/2024/001" for f in man)


# ---------------------------------------------------------------------------
# Catalogos: usuarios archivados y coherencia de es_cierre
# ---------------------------------------------------------------------------

class TestCatalogosCompletos:
    def test_incluye_usuarios_archivados_referenciados(self, odoo_helpdesk):
        # Un agente que dejo la empresa sigue asignado en tickets historicos.
        odoo_helpdesk.datos["res.users"].append({
            "id": 99, "name": "ExEmpleado", "login": "ex@empresa.com",
            "email": "ex@empresa.com", "partner_id": [599, "Ex"],
            "active": False, "share": False,
        })
        odoo_helpdesk.datos["helpdesk.ticket"][1]["user_id"] = [99, "ExEmpleado"]

        cat = exportar_catalogos(odoo_helpdesk)
        emails = {u["email"] for u in cat["usuarios"]}
        assert "ex@empresa.com" in emails, "el asignado archivado falta en el catalogo"
        # Y se marca como inactivo para que SESTIA decida que hacer con el.
        ex = next(u for u in cat["usuarios"] if u["email"] == "ex@empresa.com")
        assert ex["activo"] is False

    def test_es_cierre_respeta_el_override(self, odoo_helpdesk):
        # Con el override, "Nuevo" pasa a ser de cierre y "Resuelto" deja de serlo:
        # el catalogo debe decir lo mismo que el estado de tickets.csv.
        cat = exportar_catalogos(odoo_helpdesk, etapas_cierre=["Nuevo"])
        etapas = {e["etapa"]: e for e in cat["etapas"]}
        assert etapas["Nuevo"]["es_cierre"] is True
        assert etapas["Resuelto"]["es_cierre"] is False

    def test_es_cierre_por_defecto_usa_fold(self, odoo_helpdesk):
        etapas = {e["etapa"]: e for e in exportar_catalogos(odoo_helpdesk)["etapas"]}
        assert etapas["Resuelto"]["es_cierre"] is True
        assert etapas["Nuevo"]["es_cierre"] is False


# ---------------------------------------------------------------------------
# Clasificacion de mensajes: no debe degradar en silencio
# ---------------------------------------------------------------------------

class TestClasificacionSegura:
    def test_falla_si_no_puede_leer_los_subtipos(self, odoo_helpdesk, monkeypatch):
        # Sin subtipos no se distingue nota interna de comentario publico:
        # exportar igualmente haria publicas las notas internas del agente.
        from odoo_universal import OdooExecutionError
        original = odoo_helpdesk.execute

        def fallar(model, method, *args, **kwargs):
            if model == "mail.message.subtype":
                raise OdooExecutionError("sin permisos")
            return original(model, method, *args, **kwargs)

        monkeypatch.setattr(odoo_helpdesk, "execute", fallar)
        with pytest.raises(HelpdeskExportError, match="notas internas"):
            exportar_historial_jsonl(odoo_helpdesk)


# ---------------------------------------------------------------------------
# Validacion contra catalogos (seccion 4)
# ---------------------------------------------------------------------------

class TestValidacion:
    def test_sin_problemas_cuando_todo_esta_en_catalogo(self, odoo_helpdesk):
        r = hx.validar_contra_catalogos(odoo_helpdesk)
        assert r["ok"] is True
        assert r["tickets_revisados"] == 2

    def test_detecta_etiqueta_fuera_de_catalogo(self, odoo_helpdesk, monkeypatch):
        # El catalogo de etiquetas es CERRADO: no se crean al vuelo. Simulamos
        # que 'vip' se resuelve en el ticket pero no aparece en el catalogo.
        real = hx._catalogo_etiquetas
        monkeypatch.setattr(
            hx, "_catalogo_etiquetas",
            lambda odoo: [t for t in real(odoo) if t["nombre"] != "vip"],
        )
        r = hx.validar_contra_catalogos(odoo_helpdesk)
        assert r["ok"] is False
        assert "vip" in r["problemas"]["etiquetas"]
        assert "HT-0001" in r["problemas"]["etiquetas"]["vip"]

    def test_avisa_de_etiqueta_sin_nombre_resoluble(self, odoo_helpdesk, caplog):
        # Etiqueta borrada de helpdesk.tag pero aun referenciada por el ticket:
        # antes desaparecia del CSV sin dejar rastro.
        odoo_helpdesk.datos["helpdesk.tag"] = [{"id": 1000, "name": "urgente"}]
        with caplog.at_level("WARNING", logger="api-odoo"):
            contenido = exportar_tickets_csv(odoo_helpdesk)
        assert "sin nombre resoluble" in caplog.text
        fila = list(csv.DictReader(io.StringIO(contenido)))[0]
        assert fila["etiquetas"] == "urgente"  # la resoluble si sale

    def test_detecta_equipo_fuera_de_catalogo(self, odoo_helpdesk):
        # Equipo archivado o borrado: el ticket lo referencia pero no esta en el
        # catalogo, asi que la importacion no podria resolverlo.
        odoo_helpdesk.datos["helpdesk.team"] = []
        r = hx.validar_contra_catalogos(odoo_helpdesk)
        assert r["ok"] is False
        assert "Soporte" in r["problemas"]["equipos"]

    def test_el_asignado_siempre_entra_en_el_catalogo(self, odoo_helpdesk):
        # Contrapartida del anterior: cualquier usuario referenciado por un
        # ticket entra al catalogo, aunque sea de portal o este archivado, para
        # que la validacion de emails no lo reporte como ausente.
        odoo_helpdesk.datos["helpdesk.ticket"][0]["user_id"] = [77, "Externo"]
        odoo_helpdesk.datos["res.users"].append({
            "id": 77, "name": "Externo", "login": "externo@x.com",
            "email": "externo@x.com", "partner_id": [577, "E"],
            "active": True, "share": True,
        })
        r = hx.validar_contra_catalogos(odoo_helpdesk)
        assert "emails" not in r["problemas"]

    def test_detecta_cerrado_sin_fecha(self, odoo_helpdesk):
        odoo_helpdesk.datos["helpdesk.ticket"][1]["close_date"] = False
        r = hx.validar_contra_catalogos(odoo_helpdesk)
        assert r["ok"] is False
        assert "HT-0002" in r["problemas"]["sin_fecha_cierre"]


# ---------------------------------------------------------------------------
# Volumenes (paso 2 del plan)
# ---------------------------------------------------------------------------

class TestVolumenes:
    def test_conteos_de_tickets_mensajes_y_adjuntos(self, odoo_helpdesk):
        v = hx.contar_volumenes(odoo_helpdesk)
        assert v["tickets"] == {"total": 2, "abiertos": 1, "cerrados": 1}
        assert v["mensajes"]["total"] == 4
        assert v["mensajes"]["comentario"] == 2
        assert v["mensajes"]["nota_interna"] == 1
        assert v["mensajes"]["tracking"] == 1
        assert v["adjuntos"]["total"] == 1  # solo el ligado al ticket
        assert v["adjuntos"]["bytes"] == 1234

    def test_respeta_la_ventana_de_fechas(self, odoo_helpdesk):
        v = hx.contar_volumenes(odoo_helpdesk, desde="2024-03-01")
        assert v["tickets"]["total"] == 1
        assert v["ventana"]["desde"] == "2024-03-01"


# ---------------------------------------------------------------------------
# catalogos
# ---------------------------------------------------------------------------

class TestCatalogos:
    def test_estructura_completa(self, odoo_helpdesk):
        cat = exportar_catalogos(odoo_helpdesk)
        assert set(cat.keys()) == {
            "equipos", "etapas", "categorias", "subcategorias", "etiquetas", "usuarios",
        }
        assert cat["equipos"][0]["nombre"] == "Soporte"
        assert {t["nombre"] for t in cat["etiquetas"]} == {"urgente", "vip"}
        assert cat["usuarios"][0]["email"] == "ana@empresa.com"

    def test_etapas_marcan_inicial_y_cierre(self, odoo_helpdesk):
        etapas = {e["etapa"]: e for e in exportar_catalogos(odoo_helpdesk)["etapas"]}
        assert etapas["Nuevo"]["es_inicial"] is True
        assert etapas["Nuevo"]["es_cierre"] is False
        assert etapas["Resuelto"]["es_cierre"] is True


# ---------------------------------------------------------------------------
# HTTP: router /helpdesk/export/*
# ---------------------------------------------------------------------------

with patch("odoo_universal.OdooUniversalAPI._login", return_value=1):
    os.environ.setdefault("ODOO_URL", "https://test-odoo.com")
    os.environ.setdefault("ODOO_DB", "test-db")
    os.environ.setdefault("ODOO_USERNAME", "test-user")
    os.environ.setdefault("ODOO_PASSWORD", "test-pass")

    import api  # noqa: F401,E402
    from api import app  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import routers.helpdesk as r_helpdesk  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def sin_api_key(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)


class TestRouterHelpdesk:
    def test_tickets_csv_descarga(self, monkeypatch, odoo_helpdesk):
        monkeypatch.setattr(r_helpdesk, "resolver_tenant", lambda t="default": odoo_helpdesk)
        r = client.get("/helpdesk/export/tickets.csv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "tickets.csv" in r.headers["content-disposition"]
        assert "HT-0001" in r.text

    def test_historial_jsonl_descarga(self, monkeypatch, odoo_helpdesk):
        monkeypatch.setattr(r_helpdesk, "resolver_tenant", lambda t="default": odoo_helpdesk)
        r = client.get("/helpdesk/export/historial.jsonl")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")

    def test_adjuntos_zip_descarga(self, monkeypatch, odoo_helpdesk):
        monkeypatch.setattr(r_helpdesk, "resolver_tenant", lambda t="default": odoo_helpdesk)
        r = client.get("/helpdesk/export/adjuntos.zip")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        assert "manifiesto_adjuntos.csv" in zf.namelist()

    def test_catalogos_json(self, monkeypatch, odoo_helpdesk):
        monkeypatch.setattr(r_helpdesk, "resolver_tenant", lambda t="default": odoo_helpdesk)
        r = client.get("/helpdesk/export/catalogos")
        assert r.status_code == 200
        assert "equipos" in r.json()

    def test_volumenes_json(self, monkeypatch, odoo_helpdesk):
        monkeypatch.setattr(r_helpdesk, "resolver_tenant", lambda t="default": odoo_helpdesk)
        r = client.get("/helpdesk/export/volumenes")
        assert r.status_code == 200
        assert r.json()["tickets"]["total"] == 2

    def test_ventana_de_fechas_en_query(self, monkeypatch, odoo_helpdesk):
        monkeypatch.setattr(r_helpdesk, "resolver_tenant", lambda t="default": odoo_helpdesk)
        r = client.get("/helpdesk/export/tickets.csv?desde=2024-03-01")
        assert r.status_code == 200
        assert "HT-0001" in r.text and "HT-0002" not in r.text

    def test_validar_json(self, monkeypatch, odoo_helpdesk):
        monkeypatch.setattr(r_helpdesk, "resolver_tenant", lambda t="default": odoo_helpdesk)
        r = client.get("/helpdesk/export/validar")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_error_sin_helpdesk_devuelve_422(self, monkeypatch):
        monkeypatch.setattr(r_helpdesk, "resolver_tenant", lambda t="default": FakeOdoo({}))
        r = client.get("/helpdesk/export/tickets.csv")
        assert r.status_code == 422
