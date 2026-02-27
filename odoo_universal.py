import requests


class OdooConnectionError(Exception):
    """Error de conexion o autenticacion con Odoo."""
    pass


class OdooExecutionError(Exception):
    """Error devuelto por Odoo al ejecutar un metodo."""
    def __init__(self, message, odoo_code=None):
        super().__init__(message)
        self.odoo_code = odoo_code


class OdooUniversalAPI:
    """
    Conector generico para la API JSON-RPC de Odoo.
    Maneja autenticacion, ejecucion de metodos y errores.
    """

    def __init__(self, url: str, db: str, username: str, password: str, timeout: int = 30):
        if not all([url, db, username, password]):
            raise OdooConnectionError(
                "Faltan credenciales de Odoo. Revisa las variables de entorno."
            )
        self.url = f"{url.rstrip('/')}/jsonrpc"
        self.db = db
        self.username = username
        self.password = password
        self.timeout = timeout
        self.uid = self._login()

    def _login(self) -> int:
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": "common",
                "method": "login",
                "args": [self.db, self.username, self.password],
            },
        }
        try:
            response = requests.post(self.url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            raise OdooConnectionError(f"No se pudo conectar a Odoo: {e}") from e

        uid = data.get("result")
        if not uid:
            error = data.get("error", {})
            raise OdooConnectionError(
                f"Autenticacion fallida en Odoo: {error.get('message', 'credenciales invalidas')}"
            )
        return uid

    def execute(self, model: str, method: str, *args, **kwargs):
        """
        Ejecuta un metodo sobre un modelo de Odoo.
        Lanza OdooExecutionError si Odoo devuelve un error en la respuesta JSON-RPC.
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": "object",
                "method": "execute_kw",
                "args": [self.db, self.uid, self.password, model, method, args, kwargs],
            },
        }
        try:
            response = requests.post(self.url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            raise OdooConnectionError(f"Error de red al llamar a Odoo: {e}") from e

        if "error" in data:
            err = data["error"]
            msg = err.get("data", {}).get("message") or err.get("message", "Error desconocido de Odoo")
            raise OdooExecutionError(msg, odoo_code=err.get("code"))

        return data.get("result")


# --- Soporte multi-tenant ---

_tenants: dict[str, OdooUniversalAPI] = {}


def get_tenant(name: str) -> OdooUniversalAPI:
    """Devuelve la instancia de OdooUniversalAPI para un tenant dado."""
    if name not in _tenants:
        raise KeyError(f"Tenant '{name}' no registrado.")
    return _tenants[name]


def register_tenant(name: str, api: OdooUniversalAPI) -> None:
    """Registra una instancia de Odoo bajo un nombre de tenant."""
    _tenants[name] = api
