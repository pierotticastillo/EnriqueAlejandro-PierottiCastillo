"""Central de mantenimiento: decisiones humanas sobre órdenes y cierres.

La Central ofrece dos canales de decisión:

* el planificador aprueba o rechaza órdenes pendientes de Fase 5;
* el supervisor cierra trabajo propio y el inspector certifica u observa
  trabajo de contratista (G2/G3).

Todas las rutas de datos exigen un token Bearer. La identidad y el rol salen
del token configurado en el servidor; nunca del cuerpo HTTP. Los cuatro roles
son ``planificador``, ``supervisor``, ``inspector`` y ``lectura``.

La página es sólo un shell público sin datos: las APIs están protegidas. El
navegador guarda el token en ``sessionStorage`` y lo envía en el header
``Authorization``. No se usan cookies, por lo que este camino no introduce un
ataque CSRF clásico.

**No es una tool.** No está en el registro de tools ni en el servidor MCP: el
agente no puede llegar a este canal. Las reglas de aprobación siguen en
código; la Central sólo registra la decisión humana sobre una orden o un
cierre que el sistema dejó pendiente.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from db.repositorio import ConflictoEstado
from planta.identidad import Actor, AutenticadorToken

logger = logging.getLogger("planta.central")

#: Puerto por defecto. Es el mismo que usa la Central de la planta, para que
#: en una demostración las dos se vean iguales.
PUERTO_DEFECTO = 8200

#: Las decisiones de orden que la Central acepta.
DECISIONES = {"aprobar": "Aprobada", "rechazar": "Rechazada"}

#: Top defensivo para texto controlado por una persona. La Central muestra el
#: motivo en una pantalla y lo conserva para auditoría; no necesita campos
#: ilimitados y un texto enorme puede degradar la tabla o el registro.
MAX_MOTIVO_CARACTERES = 1000

#: Tope del cuerpo JSON de una decisión.
MAX_CUERPO_CARACTERES = 64 * 1024

#: Marcador reemplazado por un nonce único en cada respuesta HTML.
_NONCE_PLACEHOLDER = "__PIA3_CSP_NONCE__"


def _politica_csp(nonce: str | None = None) -> str:
    """La política de contenido de la Central.

    La página sólo ejecuta su propio script y su propio estilo, identificados
    por un nonce distinto en cada respuesta. No puede cargar scripts externos,
    incrustar objetos ni ser enmarcada por otro sitio. Las respuestas JSON
    reciben una política aún más restrictiva: no necesitan ejecutar nada.
    """
    if nonce is None:
        return (
            "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'none'; object-src 'none'"
        )
    return (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'none'; object-src 'none'; connect-src 'self'; "
        "img-src 'self' data:; script-src 'nonce-"
        f"{nonce}'; style-src 'nonce-{nonce}'"
    )


class Central:
    """La Central: sirve la página y recibe las decisiones humanas."""

    def __init__(
        self,
        repositorio: Any,
        *,
        publicar_orden: Any = None,
        registrar_paso: Any = None,
        decidir_cierre: Any = None,
        autenticador: AutenticadorToken | None = None,
        host: str = "127.0.0.1",
        puerto: int = PUERTO_DEFECTO,
    ) -> None:
        self._repositorio = repositorio
        self._publicar_orden = publicar_orden
        self._registrar_paso = registrar_paso
        self._decidir_cierre = decidir_cierre
        self._autenticador = autenticador or AutenticadorToken()
        self._host = host
        self._puerto = puerto
        self._servidor: ThreadingHTTPServer | None = None
        self._hilo: threading.Thread | None = None

    # ------------------------------------------------------------------ ciclo

    def iniciar(self) -> None:
        """Levanta el servidor en un hilo. Nunca tumba el sistema."""
        if self._servidor is not None:
            return
        try:
            servidor = ThreadingHTTPServer((self._host, self._puerto), _Handler)
        except OSError as exc:
            logger.warning("No se pudo abrir la Central en el puerto %s: %s", self._puerto, exc)
            return
        servidor.daemon_threads = True
        servidor.central = self  # type: ignore[attr-defined]
        self._servidor = servidor
        self._hilo = threading.Thread(
            target=servidor.serve_forever,
            kwargs={"poll_interval": 0.5},
            name="central",
            daemon=True,
        )
        self._hilo.start()
        logger.info("Central de mantenimiento en http://%s:%s", self._host, self._puerto)

    def detener(self) -> None:
        if self._servidor is None:
            return
        self._servidor.shutdown()
        self._servidor.server_close()
        self._servidor = None
        if self._hilo is not None:
            self._hilo.join(timeout=5)
            self._hilo = None

    # ------------------------------------------------------------------ actor

    @property
    def autenticador(self) -> AutenticadorToken:
        """El autenticador usado por las rutas protegidas."""
        return self._autenticador

    # ----------------------------------------------------------------- decisión

    def decidir(
        self,
        orden_id: str,
        decision: str,
        *,
        motivo: str | None = None,
        actor: Actor,
    ) -> dict[str, Any]:
        """Registra la decisión del planificador sobre una orden pendiente.

        La orden tiene que estar pendiente: una orden ya decidida no se
        cambia. El rechazo necesita motivo. El usuario persistido es el del
        actor autenticado, no un dato del cuerpo HTTP.
        """
        if actor.rol != "planificador":
            return {
                "ok": False,
                "error": "El rol no permite esta operación",
                "codigo": 403,
            }
        estado = DECISIONES.get(decision.strip().lower())
        if estado is None:
            return {
                "ok": False,
                "error": f"Decisión inválida: {decision!r}",
                "codigo": 400,
            }
        if motivo is not None and not isinstance(motivo, str):
            return {
                "ok": False,
                "error": "El motivo debe ser texto",
                "codigo": 400,
            }
        if motivo is not None and len(motivo) > MAX_MOTIVO_CARACTERES:
            return {
                "ok": False,
                "error": f"El motivo no puede superar {MAX_MOTIVO_CARACTERES} caracteres",
                "codigo": 400,
            }
        motivo = motivo.strip() if motivo is not None else None
        try:
            fila = self._repositorio.decidir_orden(
                orden_id,
                estado,
                motivo=motivo,
                usuario=actor.usuario,
                rol=actor.rol,
            )
        except ConflictoEstado as exc:
            return {"ok": False, "error": str(exc), "codigo": 409}
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "codigo": 400}
        if fila is None:
            return {
                "ok": False,
                "error": "La orden no está pendiente de aprobación",
                "codigo": 409,
            }
        self._registrar(orden_id, fila, estado, motivo, actor)
        self._publicar(fila, estado)
        return {"ok": True, "orden": fila}

    def decidir_cierre_humano(
        self,
        orden_id: str,
        decision: str,
        *,
        motivo: str | None = None,
        actor: Actor,
    ) -> dict[str, Any]:
        """Registra la decisión G2/G3 de supervisor o inspector."""
        accion = str(decision or "").strip().lower()
        if accion == "cerrar":
            permitido = actor.rol == "supervisor"
        elif accion in {"certificar", "observar"}:
            permitido = actor.rol == "inspector"
        else:
            return {
                "ok": False,
                "error": f"Decisión inválida: {decision!r}",
                "codigo": 400,
            }
        if not permitido:
            return {
                "ok": False,
                "error": "El rol no permite esta operación",
                "codigo": 403,
            }
        if motivo is not None and not isinstance(motivo, str):
            return {
                "ok": False,
                "error": "El motivo debe ser texto",
                "codigo": 400,
            }
        if motivo is not None and len(motivo) > MAX_MOTIVO_CARACTERES:
            return {
                "ok": False,
                "error": f"El motivo no puede superar {MAX_MOTIVO_CARACTERES} caracteres",
                "codigo": 400,
            }
        if self._decidir_cierre is None:
            return {
                "ok": False,
                "error": "La decisión de cierre no está disponible",
                "codigo": 501,
            }
        resultado = self._decidir_cierre(
            orden_id,
            accion,
            actor=actor.usuario,
            rol=actor.rol,
            motivo=(motivo.strip() if motivo is not None else None),
        )
        if not resultado.get("ok"):
            resultado.setdefault("codigo", 400)
            return resultado
        self._registrar_cierre(orden_id, accion, motivo, actor, resultado)
        return resultado

    def _publicar(self, fila: dict[str, Any], estado: str) -> None:
        """La orden, con su estado nuevo, sale por el bus."""
        if self._publicar_orden is None:
            return
        try:
            self._publicar_orden(fila, estado)
        except Exception:  # noqa: BLE001 - la decisión ya quedó escrita
            logger.exception("No se pudo publicar la orden con la decisión del planificador")

    def _registrar(
        self,
        orden_id: str,
        fila: dict[str, Any],
        estado: str,
        motivo: str | None,
        actor: Actor,
    ) -> None:
        if self._registrar_paso is None:
            return
        try:
            self._registrar_paso(
                etapa="planificador",
                tipo="decision",
                argumentos={
                    "decision": estado,
                    "motivo": motivo,
                    "usuario": actor.usuario,
                    "rol": actor.rol,
                },
                salida={"orden": orden_id},
                orden_id=orden_id,
                evento_id=str(fila.get("evento_id") or "") or None,
                equipo=str(fila.get("equipo") or "") or None,
            )
        except Exception:  # noqa: BLE001 - registrar no puede tumbar la Central
            logger.exception("No se pudo registrar la decisión del planificador")

    def _registrar_cierre(
        self,
        orden_id: str,
        accion: str,
        motivo: str | None,
        actor: Actor,
        resultado: dict[str, Any],
    ) -> None:
        if self._registrar_paso is None:
            return
        try:
            self._registrar_paso(
                etapa=actor.rol,
                tipo="decision_cierre",
                argumentos={
                    "decision": accion,
                    "motivo": motivo,
                    "usuario": actor.usuario,
                    "rol": actor.rol,
                },
                salida={
                    "orden": orden_id,
                    "estado": (resultado.get("orden") or {}).get("estado"),
                    "habilita": resultado.get("habilita"),
                },
                orden_id=orden_id,
            )
        except Exception:  # noqa: BLE001 - registrar no puede tumbar la Central
            logger.exception("No se pudo registrar la decisión humana del cierre")

    # -------------------------------------------------------------------- datos

    def ordenes(self, limite: int = 50) -> list[dict[str, Any]]:
        """Las órdenes que la Central tiene a la vista."""
        try:
            return self._repositorio.ordenes_para_central(limite=limite)
        except Exception:  # noqa: BLE001 - la Central no cae si la base falla
            logger.exception("No se pudieron leer las órdenes")
            return []

    def cierres(self, limite: int = 50) -> list[dict[str, Any]]:
        """Los cierres técnicos y sus decisiones humanas."""
        try:
            return self._repositorio.cierres_para_central(limite=limite)
        except Exception:  # noqa: BLE001 - la Central no cae si la base falla
            logger.exception("No se pudieron leer los cierres")
            return []

    def evidencia(self, orden_id: str) -> dict[str, Any]:
        """La evidencia y la reconstrucción append-only de una orden."""
        reconstruir = getattr(self._repositorio, "reconstruir_orden", None)
        if not callable(reconstruir):
            return {"ok": False, "error": "La evidencia no está disponible"}
        try:
            resultado = reconstruir(orden_id)
        except Exception:  # noqa: BLE001 - la Central no cae si la base falla
            logger.exception("No se pudo reconstruir la evidencia de %s", orden_id)
            return {"ok": False, "error": "No se pudo reconstruir la evidencia"}
        if resultado is None:
            return {"ok": False, "error": "La orden no existe", "codigo": 404}
        return resultado


class _Handler(BaseHTTPRequestHandler):
    """Las rutas de la Central."""

    server_version = "PIA3Central"

    @property
    def central(self) -> Central:
        return self.server.central  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 - lo llama BaseHTTPRequestHandler
        ruta = urlparse(self.path).path.rstrip("/") or "/"
        partes = ruta.split("/")
        if ruta == "/":
            # El shell no contiene datos y puede cargarse sin token. Las APIs
            # que sí muestran información exigyen autenticación.
            nonce = secrets.token_urlsafe(18)
            cuerpo = _PAGINA.replace(_NONCE_PLACEHOLDER, nonce)
            self._responder(200, cuerpo, "text/html; charset=utf-8", nonce=nonce)
            return

        actor = self._autenticar()
        if actor is None:
            return
        if ruta == "/api/sesion":
            self._responder_json(200, {"usuario": actor.usuario, "rol": actor.rol})
        elif ruta == "/api/ordenes":
            self._responder_json(200, {"ordenes": self.central.ordenes()})
        elif ruta == "/api/cierres":
            self._responder_json(200, {"cierres": self.central.cierres()})
        elif (
            len(partes) == 5
            and partes[1] == "api"
            and partes[2] == "ordenes"
            and partes[4] in {"evidencia", "reconstruccion"}
        ):
            orden_id = unquote(partes[3])
            evidencia = self.central.evidencia(orden_id)
            codigo = int(
                evidencia.get("codigo") or (503 if evidencia.get("ok") is False else 200)
            )
            self._responder_json(codigo, evidencia)
        else:
            self._responder_json(404, {"error": "No encontrado"})

    def do_POST(self) -> None:  # noqa: N802 - lo llama BaseHTTPRequestHandler
        actor = self._autenticar()
        if actor is None:
            return
        ruta = urlparse(self.path).path.rstrip("/")
        partes = ruta.split("/")
        datos = self._leer_json()

        if datos is None:
            return
        # ``usuario`` y ``rol`` se toleran para clientes antiguos, pero se
        # ignoran: la identidad viene exclusivamente del token autenticado.
        cuerpo = {
            clave: valor
            for clave, valor in datos.items()
            if clave not in {"usuario", "rol"}
        }
        unknown = set(cuerpo) - {"decision", "motivo"}
        if unknown:
            self._responder_json(
                400,
                {"ok": False, "error": f"Campos no permitidos: {sorted(unknown)!r}"},
            )
            return
        decision = cuerpo.get("decision")
        if not isinstance(decision, str):
            self._responder_json(400, {"ok": False, "error": "La decisión debe ser texto"})
            return
        motivo = cuerpo.get("motivo")
        if motivo is not None and not isinstance(motivo, str):
            self._responder_json(400, {"ok": False, "error": "El motivo debe ser texto"})
            return

        # /api/ordenes/<id>/decision
        if (
            len(partes) == 5
            and partes[1] == "api"
            and partes[2] == "ordenes"
            and partes[4] == "decision"
        ):
            if actor.rol != "planificador":
                self._responder_json(
                    403,
                    {"ok": False, "error": "El rol no permite esta operación"},
                )
                return
            resultado = self.central.decidir(
                partes[3],
                decision,
                motivo=motivo,
                actor=actor,
            )
            codigo = 200 if resultado.get("ok") else int(resultado.get("codigo") or 400)
            self._responder_json(codigo, resultado)
            return

        # /api/cierres/<orden_id>/decision
        if (
            len(partes) == 5
            and partes[1] == "api"
            and partes[2] == "cierres"
            and partes[4] == "decision"
        ):
            resultado = self.central.decidir_cierre_humano(
                partes[3],
                decision,
                motivo=motivo,
                actor=actor,
            )
            codigo = 200 if resultado.get("ok") else int(resultado.get("codigo") or 400)
            self._responder_json(codigo, resultado)
            return

        self._responder_json(404, {"error": "No encontrado"})

    # ------------------------------------------------------------------ salida

    def _autenticar(self) -> Actor | None:
        actor = self.central.autenticador.autenticar(
            self.headers.get_all("Authorization") or []
        )
        if actor is None:
            self._responder_json(
                401,
                {"ok": False, "error": "Credencial inválida o ausente"},
                cabeceras={"WWW-Authenticate": 'Bearer realm="pia3-central"'},
            )
        return actor

    def _leer_json(self) -> dict[str, Any] | None:
        try:
            longitud = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._responder_json(400, {"ok": False, "error": "Content-Length inválido"})
            return None
        if longitud < 0 or longitud > MAX_CUERPO_CARACTERES:
            self._responder_json(413, {"ok": False, "error": "El cuerpo es demasiado grande"})
            return None
        crudo = self.rfile.read(longitud) if longitud else b"{}"
        try:
            datos = json.loads(crudo.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._responder_json(400, {"ok": False, "error": "JSON inválido"})
            return None
        if not isinstance(datos, dict):
            self._responder_json(400, {"ok": False, "error": "El cuerpo debe ser un objeto"})
            return None
        return datos

    def _responder(
        self,
        codigo: int,
        cuerpo: str,
        tipo: str,
        *,
        nonce: str | None = None,
        cabeceras: dict[str, str] | None = None,
    ) -> None:
        datos = cuerpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Content-Security-Policy", _politica_csp(nonce))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        for nombre, valor in (cabeceras or {}).items():
            self.send_header(nombre, valor)
        self.end_headers()
        self.wfile.write(datos)

    def _responder_json(
        self,
        codigo: int,
        cuerpo: dict[str, Any],
        *,
        cabeceras: dict[str, str] | None = None,
    ) -> None:
        texto = json.dumps(cuerpo, ensure_ascii=False, default=str)
        self._responder(codigo, texto, "application/json", cabeceras=cabeceras)

    def log_message(self, formato: str, *args: Any) -> None:
        logger.debug("Central %s - %s", self.address_string(), formato % args)


#: La página de la Central. Es un shell sin datos: el token se carga en
#: ``sessionStorage`` y todas las APIs lo reciben en ``Authorization``.
_PAGINA = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PIA-3 · Central de mantenimiento</title>
<style nonce="__PIA3_CSP_NONCE__">
 body { font-family: system-ui, sans-serif; margin: 1.5rem; color: #222; }
 h1 { font-size: 1.3rem; } h2 { font-size: 1.05rem; margin-top: 1.5rem; }
 table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
 th, td { border: 1px solid #ccc; padding: .4rem .6rem; text-align: left;
         font-size: .9rem; vertical-align: top; }
 th { background: #f2f2f2; }
 .pendiente { background: #fff8e1; }
 .aprobada, .certificada, .cerrada { background: #e8f5e9; }
 .rechazada, .observada { background: #ffebee; }
 button { cursor: pointer; padding: .3rem .7rem; margin-right: .3rem; }
 input { padding: .3rem; width: 14rem; }
 #token { width: 24rem; }
 .vacio { color: #777; font-style: italic; }
 #estado { margin-left: .5rem; }
 .error { color: #a00; }
</style>
</head>
<body>
<h1>PIA-3 · Central de mantenimiento</h1>
<section aria-label="Acceso">
  <label for="token">Token</label>
  <input id="token" type="password" autocomplete="off" minlength="32">
  <button id="conectar" type="button">Conectar</button>
  <span id="estado">Sin sesión</span>
</section>
<p id="mensaje" class="error" role="alert"></p>

<h2>Órdenes pendientes</h2>
<p>Sólo el planificador puede aprobar o rechazar una orden.</p>
<table>
<thead><tr><th>Orden</th><th>Equipo</th><th>Estado</th><th>Tareas</th>
<th>Repuestos</th><th>Motivo</th><th>Decisión</th></tr></thead>
<tbody id="ordenes"></tbody>
</table>

<h2>Cierres y certificaciones</h2>
<p>El supervisor cierra trabajo propio; el inspector certifica u observa trabajo de contratista.</p>
<table>
<thead><tr><th>Orden</th><th>Equipo</th><th>Técnico</th><th>Empresa</th>
<th>Horas</th><th>Diferencias</th><th>Estado</th><th>Decisión</th></tr></thead>
<tbody id="cierres"></tbody>
</table>
<script nonce="__PIA3_CSP_NONCE__">
const claveToken = 'pia3-central-token';
let identidad = null;

function tokenActual() {
  return sessionStorage.getItem(claveToken) || '';
}
function mostrarMensaje(texto, esError) {
  const mensaje = document.getElementById('mensaje');
  mensaje.textContent = texto || '';
  mensaje.className = esError ? 'error' : '';
}
async function api(camino, opciones) {
  const config = Object.assign({}, opciones || {});
  config.headers = new Headers(config.headers || {});
  const token = tokenActual();
  if (token) config.headers.set('Authorization', 'Bearer ' + token);
  const respuesta = await fetch(camino, config);
  let datos = {};
  try { datos = await respuesta.json(); } catch (e) { datos = {}; }
  if (!respuesta.ok) {
    const error = new Error(datos.error || ('HTTP ' + respuesta.status));
    error.status = respuesta.status;
    throw error;
  }
  return datos;
}
function agregarCelda(fila, valor) {
  const celda = document.createElement('td');
  celda.textContent = valor === null || valor === undefined ? '' : String(valor);
  fila.appendChild(celda);
  return celda;
}
function textoTareas(valor) {
  if (!Array.isArray(valor)) return '';
  return valor.map(tarea => {
    if (typeof tarea === 'string') return tarea;
    try { return JSON.stringify(tarea) || ''; } catch (e) { return ''; }
  }).join(', ');
}
function textoRepuestos(valor) {
  if (!Array.isArray(valor)) return '';
  return valor.map(repuesto => {
    if (!repuesto || typeof repuesto !== 'object') return String(repuesto ?? '');
    const codigo = String(repuesto.codigo ?? '');
    const cantidad = Number(repuesto.cantidad ?? 0);
    return codigo + ' x' + (Number.isFinite(cantidad) ? String(cantidad) : '0');
  }).join(', ');
}
function textoDiferencias(valor) {
  if (!Array.isArray(valor)) return '';
  return valor.map(diferencia => {
    if (!diferencia || typeof diferencia !== 'object') return String(diferencia ?? '');
    return String(diferencia.detalle ?? diferencia.tipo ?? '');
  }).join('; ');
}
function claseEstado(estado) {
  if (estado === 'Aprobada' || estado === 'Cerrada' || estado === 'Certificada') return 'aprobada';
  if (estado === 'Rechazada' || estado === 'Observada') return 'rechazada';
  return 'pendiente';
}
function campoMotivo() {
  const motivo = document.createElement('input');
  motivo.placeholder = 'motivo (obligatorio al observar o rechazar)';
  motivo.maxLength = 1000;
  return motivo;
}
function boton(texto, accion) {
  const control = document.createElement('button');
  control.type = 'button';
  control.textContent = texto;
  control.onclick = accion;
  return control;
}
async function decidirOrden(id, decision, motivo) {
  try {
    await api('/api/ordenes/' + encodeURIComponent(id) + '/decision', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({decision: decision, motivo: motivo})
    });
    mostrarMensaje('Decisión registrada.', false);
    await cargar();
  } catch (e) { mostrarMensaje(e.message, true); }
}
async function decidirCierre(id, decision, motivo) {
  try {
    await api('/api/cierres/' + encodeURIComponent(id) + '/decision', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({decision: decision, motivo: motivo})
    });
    mostrarMensaje('Cierre registrado.', false);
    await cargar();
  } catch (e) { mostrarMensaje(e.message, true); }
}
function renderOrdenes(ordenes) {
  const caja = document.getElementById('ordenes');
  if (!ordenes.length) {
    const fila = document.createElement('tr');
    const celda = document.createElement('td');
    celda.colSpan = 7;
    celda.className = 'vacio';
    celda.textContent = 'No hay órdenes.';
    fila.appendChild(celda);
    caja.replaceChildren(fila);
    return;
  }
  caja.replaceChildren();
  for (const orden of ordenes) {
    if (!orden || typeof orden !== 'object') continue;
    const pendiente = orden.estado === 'Pendiente de aprobacion';
    const fila = document.createElement('tr');
    fila.className = claseEstado(orden.estado);
    agregarCelda(fila, orden.id);
    agregarCelda(fila, orden.equipo);
    agregarCelda(fila, orden.estado);
    agregarCelda(fila, textoTareas(orden.tareas));
    agregarCelda(fila, textoRepuestos(orden.repuestos));
    agregarCelda(fila, orden.motivo);
    const celda = document.createElement('td');
    if (pendiente && identidad && identidad.rol === 'planificador') {
      const motivo = campoMotivo();
      celda.appendChild(motivo);
      celda.appendChild(boton('Aprobar', () => decidirOrden(
        String(orden.id ?? ''), 'aprobar', motivo.value
      )));
      celda.appendChild(boton('Rechazar', () => decidirOrden(
        String(orden.id ?? ''), 'rechazar', motivo.value
      )));
    } else { celda.textContent = '—'; }
    fila.appendChild(celda);
    caja.appendChild(fila);
  }
}
function renderCierres(cierres) {
  const caja = document.getElementById('cierres');
  if (!cierres.length) {
    const fila = document.createElement('tr');
    const celda = document.createElement('td');
    celda.colSpan = 8;
    celda.className = 'vacio';
    celda.textContent = 'No hay cierres.';
    fila.appendChild(celda);
    caja.replaceChildren(fila);
    return;
  }
  caja.replaceChildren();
  for (const cierre of cierres) {
    if (!cierre || typeof cierre !== 'object') continue;
    const fila = document.createElement('tr');
    fila.className = claseEstado(cierre.estado);
    agregarCelda(fila, cierre.orden_id);
    agregarCelda(fila, cierre.equipo);
    agregarCelda(fila, cierre.tecnico);
    agregarCelda(fila, cierre.empresa);
    agregarCelda(fila, cierre.horas);
    agregarCelda(fila, textoDiferencias(cierre.diferencias));
    agregarCelda(fila, cierre.estado);
    const celda = document.createElement('td');
    const propio = String(cierre.ejecutor || 'propio').toLowerCase() === 'propio';
    if (cierre.estado === 'Ejecutada' && identidad) {
      const motivo = campoMotivo();
      celda.appendChild(motivo);
      if (identidad.rol === 'supervisor' && propio) {
        celda.appendChild(boton('Cerrar', () => decidirCierre(
          String(cierre.orden_id ?? ''), 'cerrar', motivo.value
        )));
      }
      if (identidad.rol === 'inspector' && !propio) {
        celda.appendChild(boton('Certificar', () => decidirCierre(
          String(cierre.orden_id ?? ''), 'certificar', motivo.value
        )));
        celda.appendChild(boton('Observar', () => decidirCierre(
          String(cierre.orden_id ?? ''), 'observar', motivo.value
        )));
      }
    }
    if (!celda.childNodes.length) celda.textContent = '—';
    fila.appendChild(celda);
    caja.appendChild(fila);
  }
}
async function cargar() {
  if (!tokenActual()) {
    identidad = null;
    document.getElementById('estado').textContent = 'Sin sesión';
    return;
  }
  try {
    const sesion = await api('/api/sesion');
    identidad = sesion;
    document.getElementById('estado').textContent = sesion.usuario + ' · ' + sesion.rol;
    const datos = await Promise.all([api('/api/ordenes'), api('/api/cierres')]);
    renderOrdenes(Array.isArray(datos[0].ordenes) ? datos[0].ordenes : []);
    renderCierres(Array.isArray(datos[1].cierres) ? datos[1].cierres : []);
    mostrarMensaje('', false);
  } catch (e) {
    identidad = null;
    document.getElementById('estado').textContent = 'Sesión inválida';
    mostrarMensaje(e.message, true);
  }
}
document.getElementById('conectar').onclick = async () => {
  const token = document.getElementById('token').value.trim();
  if (!token) { mostrarMensaje('Ingresá un token.', true); return; }
  sessionStorage.setItem(claveToken, token);
  await cargar();
};
document.getElementById('token').value = tokenActual();
cargar();
setInterval(cargar, 20000);
</script>
</body>
</html>
"""
