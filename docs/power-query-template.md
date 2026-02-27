# Plantilla Power Query M para Excel

## Requisitos previos
- Excel 365 o Excel 2019+
- El middleware API-Odoo corriendo localmente o en red

## Pasos

1. En Excel, ve a **Datos > Obtener datos > Desde otras fuentes > Consulta en blanco**
2. En el editor de Power Query, pega el siguiente codigo M:

```m
let
    // === CONFIGURACION ===
    ApiUrl = "http://localhost:8000/odoo",
    ApiKey = "tu-clave-aqui",

    // === PARAMETROS DE LA CONSULTA ===
    Modelo   = "account.move",
    Metodo   = "search_read",
    Filtros  = {{{  "state", "=", "posted" }, { "move_type", "=", "out_invoice" }}},
    Campos   = {"name", "invoice_date", "amount_total", "partner_id"},
    Limite   = 500,

    // === CONSTRUCCION DEL BODY ===
    Cuerpo = Json.FromValue([
        model  = Modelo,
        method = Metodo,
        args   = {Filtros},
        kwargs = [fields = Campos, limit = Limite]
    ]),

    // === LLAMADA HTTP ===
    Respuesta = Web.Contents(
        ApiUrl,
        [
            Headers = [
                #"Content-Type" = "application/json",
                #"X-Api-Key"    = ApiKey
            ],
            Content = Cuerpo
        ]
    ),

    // === PROCESAR RESPUESTA ===
    Json        = Json.Document(Respuesta),
    Registros   = Json[result],
    Tabla       = Table.FromList(Registros, Splitter.SplitByNothing()),
    Expandida   = Table.ExpandRecordColumn(Tabla, "Column1", Record.FieldNames(Tabla{0}[Column1]))
in
    Expandida
```

3. Haz clic en **Cerrar y cargar** para traer los datos a la hoja.
4. Para actualizar, usa **Datos > Actualizar todo** o programa una actualizacion automatica.

## Personalizar la consulta

Cambia estos valores segun lo que necesites:

| Variable | Descripcion | Ejemplo |
|----------|------------|---------|
| `Modelo` | Modelo de Odoo | `"sale.order"`, `"res.partner"` |
| `Metodo` | Metodo a ejecutar | `"search_read"`, `"read"` |
| `Filtros` | Condiciones de busqueda | `{{"state", "=", "sale"}}` |
| `Campos` | Campos a traer | `{"name", "amount_total"}` |
| `Limite` | Numero maximo de registros | `1000` |

## Notas
- Si Odoo devuelve campos anidados (como `partner_id` que es `[id, nombre]`), usa `Table.ExpandListColumn` adicionales.
- Para programar actualizacion automatica, configura el refresco en las propiedades de la consulta.
