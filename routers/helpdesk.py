"""
Router de EXPORTACION de Helpdesk: genera los archivos de migracion Odoo -> SESTIA.

Todos los endpoints son de SOLO LECTURA sobre Odoo. Producen los archivos que la
especificacion de migracion pide al equipo de Odoo (sin escribir nada en Odoo):

  GET /helpdesk/export/volumenes       -> conteos previos (paso 2 del plan)
  GET /helpdesk/export/tickets.csv     -> un ticket por fila (CSV RFC 4180)
  GET /helpdesk/export/historial.jsonl -> un mensaje del chatter por linea (JSONL)
  GET /helpdesk/export/adjuntos.zip    -> binarios + manifiesto_adjuntos.csv
  GET /helpdesk/export/catalogos.zip   -> un CSV por catalogo (equipos, etapas...)
  GET /helpdesk/export/catalogos       -> los catalogos como JSON (inspeccion rapida)

Parametros comunes (query string):
  tenant   nombre del tenant Odoo (por defecto "default").
  limite   maximo de tickets a exportar (para la MUESTRA de 10-20; None = todos).
  desde    fecha de corte inferior (ISO 8601) sobre write_date del ticket.
  hasta    fecha de corte superior (ISO 8601).

`desde`/`hasta` son el mecanismo de RE-EXPORTACION INCREMENTAL del paso 8: tras
la exportacion completa, una segunda pasada con ?desde=<fecha de corte> trae solo
los tickets creados o modificados despues. El odoo_ref evita duplicados.

Para tickets.csv, ademas:
  etapas_cierre  nombres de etapas de cierre separados por coma. Si se omite, el
                 estado open/closed se deriva del campo `fold` de la etapa.
Para historial.jsonl, ademas:
  incluir_tracking  si "true", incluye las notificaciones automaticas de tracking
                    (por defecto se omiten, como recomienda la especificacion).
  recortar_citas    si "true" (por defecto), recorta el hilo citado y las firmas.
  solo_abiertos     si "true", historial solo de los tickets abiertos (seccion 7).
Para adjuntos.zip, ademas:
  incluir_embebidas si "true" (por defecto), rescata las imagenes embebidas en el
                    cuerpo de los mensajes, que mueren al apagar Odoo (5.3).
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from core.helpdesk_export import (
    HelpdeskExportError,
    catalogos_a_zip,
    contar_volumenes,
    exportar_adjuntos_zip,
    exportar_catalogos,
    exportar_historial_jsonl,
    exportar_tickets_csv,
)
from core.seguridad import resolver_tenant, verify_api_key
from odoo_universal import OdooConnectionError, OdooUniversalAPI

logger = logging.getLogger("api-odoo")

router = APIRouter(
    prefix="/helpdesk/export",
    tags=["Helpdesk Export"],
    dependencies=[Depends(verify_api_key)],
)


def _etapas_cierre_por_defecto() -> list[str] | None:
    """Lee la lista de etapas de cierre del entorno (HELPDESK_ETAPAS_CIERRE)."""
    crudo = os.getenv("HELPDESK_ETAPAS_CIERRE", "")
    nombres = [n.strip() for n in crudo.split(",") if n.strip()]
    return nombres or None


def _manejar_errores(fn):
    """Traduce los errores de exportacion a codigos HTTP coherentes con la app."""
    try:
        return fn()
    except HelpdeskExportError as e:
        logger.warning("HELPDESK_EXPORT_ERROR | %s", e)
        raise HTTPException(status_code=422, detail=str(e))
    except OdooConnectionError as e:
        logger.error("HELPDESK_EXPORT_CONEXION | %s", e)
        raise HTTPException(status_code=503, detail=f"Error de conexion con Odoo: {e}")


@router.get("/volumenes")
def consultar_volumenes(
    tenant: str = "default",
    desde: str | None = Query(None, description="Fecha de corte inferior (ISO 8601)."),
    hasta: str | None = Query(None, description="Fecha de corte superior (ISO 8601)."),
):
    """
    Conteos previos a la exportacion (paso 2 del plan): tickets abiertos/cerrados,
    mensajes por tipo y numero/peso de adjuntos. Sirve para decidir el alcance del
    historial y si el ZIP de adjuntos hay que trocearlo.
    """
    odoo: OdooUniversalAPI = resolver_tenant(tenant)
    return _manejar_errores(lambda: contar_volumenes(odoo, desde=desde, hasta=hasta))


@router.get("/tickets.csv")
def exportar_tickets(
    tenant: str = "default",
    limite: int | None = Query(None, ge=1, description="Maximo de tickets (muestra)."),
    etapas_cierre: str | None = Query(
        None, description="Nombres de etapas de cierre separados por coma (override de fold)."
    ),
    desde: str | None = Query(None, description="Fecha de corte inferior (ISO 8601)."),
    hasta: str | None = Query(None, description="Fecha de corte superior (ISO 8601)."),
):
    """Genera tickets.csv (UTF-8, RFC 4180). Un ticket por fila."""
    odoo: OdooUniversalAPI = resolver_tenant(tenant)
    cierre = (
        [n.strip() for n in etapas_cierre.split(",") if n.strip()]
        if etapas_cierre is not None
        else _etapas_cierre_por_defecto()
    )
    contenido = _manejar_errores(
        lambda: exportar_tickets_csv(
            odoo, limite=limite, etapas_cierre=cierre, desde=desde, hasta=hasta
        )
    )
    return Response(
        content=contenido.encode("utf-8-sig"),  # BOM: Excel abre bien el UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="tickets.csv"'},
    )


@router.get("/historial.jsonl")
def exportar_historial(
    tenant: str = "default",
    limite: int | None = Query(None, ge=1, description="Maximo de tickets (muestra)."),
    incluir_tracking: bool = Query(
        False, description="Incluir notificaciones de tracking (por defecto se omiten)."
    ),
    recortar_citas: bool = Query(
        True, description="Recortar el hilo citado y las firmas de los correos (5.3)."
    ),
    solo_abiertos: bool = Query(
        False, description="Historial solo de los tickets abiertos (alcance, seccion 7)."
    ),
    etapas_cierre: str | None = Query(
        None, description="Etapas de cierre separadas por coma (para solo_abiertos)."
    ),
    desde: str | None = Query(None, description="Fecha de corte inferior (ISO 8601)."),
    hasta: str | None = Query(None, description="Fecha de corte superior (ISO 8601)."),
):
    """Genera historial.jsonl: un mensaje del chatter por linea."""
    odoo: OdooUniversalAPI = resolver_tenant(tenant)
    cierre = (
        [n.strip() for n in etapas_cierre.split(",") if n.strip()]
        if etapas_cierre is not None
        else _etapas_cierre_por_defecto()
    )
    contenido = _manejar_errores(
        lambda: exportar_historial_jsonl(
            odoo, limite=limite, incluir_tracking=incluir_tracking,
            desde=desde, hasta=hasta, recortar_citas=recortar_citas,
            solo_abiertos=solo_abiertos, etapas_cierre=cierre,
        )
    )
    return Response(
        content=contenido.encode("utf-8"),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="historial.jsonl"'},
    )


@router.get("/adjuntos.zip")
def exportar_adjuntos(
    tenant: str = "default",
    limite: int | None = Query(None, ge=1, description="Maximo de tickets (muestra)."),
    incluir_embebidas: bool = Query(
        True, description="Rescatar las imagenes embebidas en el cuerpo de los mensajes (5.3)."
    ),
    desde: str | None = Query(None, description="Fecha de corte inferior (ISO 8601)."),
    hasta: str | None = Query(None, description="Fecha de corte superior (ISO 8601)."),
):
    """
    Genera adjuntos.zip con los binarios + manifiesto_adjuntos.csv.

    Para volumenes grandes, trocear la entrega usando la ventana de fechas
    (?desde=&hasta=) o el limite de tickets, como anticipa la seccion 6.
    """
    odoo: OdooUniversalAPI = resolver_tenant(tenant)
    contenido = _manejar_errores(
        lambda: exportar_adjuntos_zip(
            odoo, limite=limite, desde=desde, hasta=hasta,
            incluir_embebidas=incluir_embebidas,
        )
    )
    return Response(
        content=contenido,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="adjuntos.zip"'},
    )


@router.get("/catalogos.zip")
def exportar_catalogos_zip(tenant: str = "default"):
    """Genera un ZIP con un CSV por catalogo (equipos, etapas, categorias...)."""
    odoo: OdooUniversalAPI = resolver_tenant(tenant)
    contenido = _manejar_errores(lambda: catalogos_a_zip(exportar_catalogos(odoo)))
    return Response(
        content=contenido,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="catalogos.zip"'},
    )


@router.get("/catalogos")
def consultar_catalogos(tenant: str = "default"):
    """Devuelve los catalogos como JSON (inspeccion rapida antes de importar)."""
    odoo: OdooUniversalAPI = resolver_tenant(tenant)
    return _manejar_errores(lambda: exportar_catalogos(odoo))
