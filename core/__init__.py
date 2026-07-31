"""
Paquete core del middleware API-Odoo (exportacion de Helpdesk).

Logica construida sobre el conector de transporte (odoo_universal.py):

  - helpdesk_export: exportacion de SOLO LECTURA de tickets, historial,
                     adjuntos y catalogos para la migracion Odoo -> SESTIA
  - seguridad:       dependencias FastAPI compartidas (API key, tenant)

La capa contable con estado (state store, mapper, sincronizador, facturacion,
pagos, conciliacion, impuestos, inventario, cola Celery y rollback) se retiro
de este repo: no forma parte de la especificacion de migracion. Sigue viva en
el middleware original (repo API-Odoo).
"""
