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
    """Filtro minimo: soporta ('campo','=',v) y ('campo','in',[...])."""
    out = registros
    for cond in dominio:
        if not isinstance(cond, (list, tuple)) or len(cond) != 3:
            continue
        campo, op, valor = cond
        if op == "=":
            out = [r for r in out if _valor_campo(r, campo) == valor]
        elif op == "in":
            out = [r for r in out if _valor_campo(r, campo) in valor]
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
                "sla_deadline": {"type": "datetime"},
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
                "partner_email": "cliente@x.com", "partner_phone": "555",
                "partner_name": "Cliente X", "sla_deadline": "2024-03-16 10:30:00",
                "category_id": [7, "Hardware"],
            },
            {
                "id": 2, "ticket_ref": "HT-0002", "name": "Duda facturacion",
                "description": "texto plano", "team_id": [10, "Soporte"],
                "stage_id": [103, "Resuelto"], "priority": "0", "tag_ids": [],
                "user_id": False, "create_uid": [50, "Ana"],
                "create_date": "2024-02-01 09:00:00",
                "close_date": "2024-02-02 12:00:00",
                "partner_email": False, "partner_phone": False,
                "partner_name": False, "sla_deadline": False, "category_id": False,
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
        ],
        "ir.attachment": [
            {
                "id": 7000, "res_model": "helpdesk.ticket", "res_id": 1,
                "name": "foto.png", "mimetype": "image/png",
                "file_size": 1234, "create_date": "2024-03-15 11:00:00",
                "create_uid": [50, "Ana"], "datas": base64.b64encode(b"PNGDATA").decode(),
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
        assert list(filas[0].keys()) == hx.COLUMNAS_TICKETS

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
        assert tipos == ["comentario", "nota_interna"]

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
        assert "1/foto.png" in nombres
        assert zf.read("1/foto.png") == b"PNGDATA"

    def test_manifiesto_liga_adjunto_a_mensaje(self, odoo_helpdesk):
        zf = zipfile.ZipFile(io.BytesIO(exportar_adjuntos_zip(odoo_helpdesk)))
        manifiesto = list(csv.DictReader(io.StringIO(zf.read("manifiesto_adjuntos.csv").decode())))
        fila = manifiesto[0]
        assert fila["odoo_attachment_id"] == "7000"
        assert fila["odoo_message_id"] == "9001"  # adjunto ligado al mensaje
        assert fila["mimetype"] == "image/png"
        assert fila["ruta_en_zip"] == "1/foto.png"


# ---------------------------------------------------------------------------
# catalogos
# ---------------------------------------------------------------------------

class TestCatalogos:
    def test_estructura_completa(self, odoo_helpdesk):
        cat = exportar_catalogos(odoo_helpdesk)
        assert set(cat.keys()) == {"equipos", "etapas", "categorias", "etiquetas", "usuarios"}
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

    def test_error_sin_helpdesk_devuelve_422(self, monkeypatch):
        monkeypatch.setattr(r_helpdesk, "resolver_tenant", lambda t="default": FakeOdoo({}))
        r = client.get("/helpdesk/export/tickets.csv")
        assert r.status_code == 422
