"""Central de mantenimiento: donde el planificador decide sobre las órdenes.

El enunciado pide, en la sección D: *"Mientras una orden está pendiente, el
proceso queda suspendido y persistido: sobrevive a un reinicio del sistema y
se reanuda cuando el planificador la aprueba o la rechaza"*.

Persistir y survive ya estaba hecho (la orden queda en la base y se vuelve a
publicar con el mismo id). Lo que faltaba era **el canal de la decisión**:
una forma de que la persona que decide deje su decisión escrita.

Esta es esa forma:

* una página web mínima con las órdenes que esperan decisión y dos botones
  (aprobar / rechazar), como la Central de la planta;
* una API JSON equivalente, para integraciones;
* la decisión queda en la tabla ``aprobacion`` con su usuario y su motivo, y
  en el registro de la trayectoria.

**No es una tool.** No está en el registro de tools ni en el servidor MCP: el
agente no puede llegar a este canal. La única escritura sobre órdenes sigue
siendo la que autorizan las reglas de aprobación (enunciado, sección F) y,
ahora, la decisión de una persona sobre una orden que las reglas dejaron
pendiente.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("planta.central")

#: Puerto por defecto. Es el mismo que usa la Central de la planta, para que
#: en una demostración las dos se vean iguales.
PUERTO_DEFECTO = 8200

#: Las decisiones que la Central acepta.
DECISIONES = {"aprobar": "Aprobada", "rechazar": "Rechazada"}


class Central:
    """La Central: sirve la página y recibe las decisiones del planificador."""

    def __init__(
        self,
        repositorio: Any,
        *,
        publicar_orden: Any = None,
        registrar_paso: Any = None,
        host: str = "0.0.0.0",
        puerto: int = PUERTO_DEFECTO,
    ) -> None:
        self._repositorio = repositorio
        self._publicar_orden = publicar_orden
        self._registrar_paso = registrar_paso
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

    # ----------------------------------------------------------------- decisión

    def decidir(
        self,
        orden_id: str,
        decision: str,
        *,
        motivo: str | None = None,
        usuario: str | None = None,
    ) -> dict[str, Any]:
        """Registra la decisión del planificador sobre una orden pendiente.

        La orden tiene que estar pendiente: una orden ya decidida no se
        cambia. El rechazo necesita motivo.
        """
        estado = DECISIONES.get(decision.strip().lower())
        if estado is None:
            return {"ok": False, "error": f"Decisión inválida: {decision!r}"}
        try:
            fila = self._repositorio.decidir_orden(
                orden_id,
                estado,
                motivo=motivo,
                usuario=usuario,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if fila is None:
            return {"ok": False, "error": "La orden no está pendiente de aprobación"}
        self._registrar(orden_id, fila, estado, motivo, usuario)
        self._publicar(fila, estado)
        return {"ok": True, "orden": fila}

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
        usuario: str | None,
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
                    "usuario": usuario,
                },
                salida={"orden": orden_id},
                orden_id=orden_id,
                evento_id=str(fila.get("evento_id") or "") or None,
                equipo=str(fila.get("equipo") or "") or None,
            )
        except Exception:  # noqa: BLE001 - registrar no puede tumbar la Central
            logger.exception("No se pudo registrar la decisión del planificador")

    # -------------------------------------------------------------------- datos

    def ordenes(self, limite: int = 50) -> list[dict[str, Any]]:
        """Las órdenes que el planificador tiene a la vista."""
        try:
            return self._repositorio.ordenes_para_central(limite=limite)
        except Exception:  # noqa: BLE001 - la Central no cae si la base falla
            logger.exception("No se pudieron leer las órdenes")
            return []


class _Handler(BaseHTTPRequestHandler):
    """Las rutas de la Central."""

    server_version = "PIA3Central"

    @property
    def central(self) -> Central:
        return self.server.central  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 - lo llama BaseHTTPRequestHandler
        ruta = urlparse(self.path).path.rstrip("/") or "/"
        if ruta == "/":
            self._responder(200, _PAGINA, "text/html; charset=utf-8")
        elif ruta == "/api/ordenes":
            self._responder_json(200, {"ordenes": self.central.ordenes()})
        else:
            self._responder_json(404, {"error": "No encontrado"})

    def do_POST(self) -> None:  # noqa: N802 - lo llama BaseHTTPRequestHandler
        ruta = urlparse(self.path).path.rstrip("/")
        partes = ruta.split("/")
        # /api/ordenes/<id>/decision
        if (
            len(partes) != 5
            or partes[1] != "api"
            or partes[2] != "ordenes"
            or partes[4] != "decision"
        ):
            self._responder_json(404, {"error": "No encontrado"})
            return
        orden_id = partes[3]
        try:
            longitud = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            longitud = 0
        crudo = self.rfile.read(longitud) if longitud else b"{}"
        try:
            datos = json.loads(crudo.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._responder_json(400, {"ok": False, "error": "JSON inválido"})
            return
        if not isinstance(datos, dict):
            self._responder_json(400, {"ok": False, "error": "El cuerpo debe ser un objeto"})
            return
        resultado = self.central.decidir(
            orden_id,
            str(datos.get("decision") or ""),
            motivo=datos.get("motivo"),
            usuario=datos.get("usuario"),
        )
        self._responder_json(200 if resultado.get("ok") else 400, resultado)

    # ------------------------------------------------------------------ salida

    def _responder(self, codigo: int, cuerpo: str, tipo: str) -> None:
        datos = cuerpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _responder_json(self, codigo: int, cuerpo: dict[str, Any]) -> None:
        texto = json.dumps(cuerpo, ensure_ascii=False, default=str)
        self._responder(codigo, texto, "application/json")

    def log_message(self, formato: str, *args: Any) -> None:
        logger.debug("Central %s - %s", self.address_string(), formato % args)


#: La página de la Central. Es una sola pantalla: las órdenes que esperan
#: decisión, con sus tareas, sus repuestos y su motivo.
_PAGINA = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>PIA-3 · Central de mantenimiento</title>
<style>
 body { font-family: system-ui, sans-serif; margin: 1.5rem; color: #222; }
 h1 { font-size: 1.3rem; }
 table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
 th, td { border: 1px solid #ccc; padding: .4rem .6rem; text-align: left; font-size: .9rem; }
 th { background: #f2f2f2; }
 .pendiente { background: #fff8e1; }
 .aprobada { background: #e8f5e9; }
 .rechazada { background: #ffebee; }
 button { cursor: pointer; padding: .3rem .7rem; margin-right: .3rem; }
 input { padding: .3rem; width: 14rem; }
 .vacio { color: #777; font-style: italic; }
</style>
</head>
<body>
<h1>PIA-3 · Central de mantenimiento</h1>
<p>Órdenes con las reglas ya evaluadas. Sólo se pueden decidir las que están
<b>pendientes de aprobación</b>.</p>
<table>
<thead><tr><th>Orden</th><th>Equipo</th><th>Estado</th><th>Tareas</th>
<th>Repuestos</th><th>Motivo</th><th>Decisión</th></tr></thead>
<tbody id="filas"></tbody>
</table>
<script>
async function cargar() {
  const caja = document.getElementById('filas');
  let datos;
  try {
    datos = await (await fetch('/api/ordenes')).json();
  } catch (e) { return; }
  const ordenes = datos.ordenes || [];
  if (!ordenes.length) {
    caja.innerHTML = '<tr><td colspan="7" class="vacio">No hay órdenes.</td></tr>';
    return;
  }
  caja.innerHTML = '';
  for (const o of ordenes) {
    const pendiente = o.estado === 'Pendiente de aprobacion';
    const clase = o.estado === 'Aprobada' ? 'aprobada'
                : o.estado === 'Rechazada' ? 'rechazada' : 'pendiente';
    const tr = document.createElement('tr');
    tr.className = clase;
    tr.innerHTML = '<td>' + o.id + '</td><td>' + o.equipo + '</td><td>' + o.estado
      + '</td><td>' + (o.tareas || []).join(', ') + '</td><td>'
      + (o.repuestos || []).map(r => r.codigo + ' x' + r.cantidad).join(', ')
      + '</td><td>' + (o.motivo || '') + '</td>';
    const celda = document.createElement('td');
    if (pendiente) {
      const motivo = document.createElement('input');
      motivo.placeholder = 'motivo (obligatorio al rechazar)';
      celda.appendChild(motivo);
      const ok = document.createElement('button');
      ok.textContent = 'Aprobar';
      ok.onclick = () => decidir(o.id, 'aprobar', motivo.value);
      const no = document.createElement('button');
      no.textContent = 'Rechazar';
      no.onclick = () => decidir(o.id, 'rechazar', motivo.value);
      celda.appendChild(ok); celda.appendChild(no);
    } else { celda.textContent = '—'; }
    tr.appendChild(celda);
    caja.appendChild(tr);
  }
}
async function decidir(id, decision, motivo) {
  await fetch('/api/ordenes/' + encodeURIComponent(id) + '/decision', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({decision: decision, motivo: motivo, usuario: 'planificador'})
  });
  cargar();
}
cargar();
setInterval(cargar, 20000);
</script>
</body>
</html>
"""
