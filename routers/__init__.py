"""
Routers del middleware API-Odoo (exportacion de Helpdesk).

  - helpdesk: GET /helpdesk/export/*  -> archivos de migracion Odoo -> SESTIA
              (solo lectura: no escribe nada en Odoo)

Se montan en api.py con app.include_router(...).
"""
