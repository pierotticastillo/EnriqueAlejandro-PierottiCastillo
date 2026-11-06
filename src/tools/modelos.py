"""Modelos Pydantic de las tools: entrada y salida.

Todos heredan de ``ModeloEstricto``, que forbid extra campos: una tool que
reciba un campo que no conoce falla en vez de ignorarlo en silencio.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ModeloEstricto(BaseModel):
    """Base de todos los modelos: sin campos extra."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------- equipos


class ConsultaEquipos(ModeloEstricto):
    """Filtros de la consulta de equipos."""

    tag: str | None = None
    tipo: str | None = None


class ManometroLocal(ModeloEstricto):
    unidad: str | None = None


class Sensor(ModeloEstricto):
    """Sensor del catálogo con sus límites de alarma."""

    tag: str
    variable: str
    unidad: str | None = None
    alarma_baja: float | None = None
    alarma_alta: float | None = None
    disparo: float | None = None


class Equipo(ModeloEstricto):
    """Equipo de la planta con sus nombres de campo."""

    tag: str
    tipo: str | None = None
    descripcion: str | None = None
    fabricante: str | None = None
    modelo: str | None = None
    nombres: list[str] = Field(default_factory=list)
    caudal_nominal_m3d: float | None = None
    manometro_local: ManometroLocal | None = None
    rol_inicial: str | None = None
    sensores: list[Sensor] = Field(default_factory=list)


# ------------------------------------------------------------------- historial


class ConsultaHistorialSensores(ModeloEstricto):
    """Ventana del historial de un sensor."""

    tag: str
    variable: str
    desde: datetime | None = None
    hasta: datetime | None = None
    limite: int = Field(default=500, ge=1, le=5000)


class MuestraSensor(ModeloEstricto):
    ts: datetime
    valor: float | None = None


class HistorialSensores(ModeloEstricto):
    """Serie de un sensor con un resumen de la ventana consultada."""

    tag: str
    variable: str
    unidad: str | None = None
    cantidad: int
    desde: datetime | None = None
    hasta: datetime | None = None
    minimo: float | None = None
    maximo: float | None = None
    promedio: float | None = None
    tendencia: str | None = None
    muestras: list[MuestraSensor] = Field(default_factory=list)


class ConsultaHistorialOrdenes(ModeloEstricto):
    """Filtros del historial de órdenes."""

    equipo: str | None = None
    desde: datetime | None = None
    hasta: datetime | None = None
    limite: int = Field(default=50, ge=1, le=500)


class OrdenHistorico(ModeloEstricto):
    """Orden del archivo histórico de la planta."""

    orden_id: str
    equipo: str | None = None
    fecha: datetime | None = None
    tareas: list[str] = Field(default_factory=list)
    ejecutor: str | None = None
    estado: str | None = None
    repuestos: list[str] = Field(default_factory=list)
    texto: str | None = None


# ------------------------------------------------------------------ inventario


class ConsultaInventario(ModeloEstricto):
    """Filtros del stock de repuestos."""

    codigo: str | None = None
    equipo: str | None = None
    almacen: str = Field(default="yacimiento", pattern="^(yacimiento|central)$")


class RepuestoStock(ModeloEstricto):
    """Repuesto con su stock por almacén."""

    codigo: str
    nombre: str | None = None
    unidad: str | None = None
    costo_usd: float | None = None
    stock: dict[str, float] = Field(default_factory=dict)
    disponible: bool = False
    equipos: list[str] = Field(default_factory=list)


class ConsultaListaMateriales(ModeloEstricto):
    """Materiales que le corresponden a un equipo."""

    equipo: str


class MaterialEquipo(ModeloEstricto):
    codigo: str
    nombre: str | None = None
    costo_usd: float | None = None


# --------------------------------------------------------------------- tareas


class ConsultaCatalogoTareas(ModeloEstricto):
    """Filtros del catálogo de tareas del plan de mantenimiento."""

    equipo: str | None = None
    codigo: str | None = None
    incluir_no_estandar: bool = True


class RepuestoTarea(ModeloEstricto):
    codigo: str
    cantidad: float


class TareaCatalogo(ModeloEstricto):
    """Tarea del catálogo con su ejecutor, repuestos y cita del manual."""

    codigo: str
    nombre: str
    equipos: list[str] = Field(default_factory=list)
    detiene: bool = False
    horas: float | None = None
    ejecutor: str | None = None
    estandar: bool = True
    repuestos: list[RepuestoTarea] = Field(default_factory=list)
    costo_repuestos_usd: float | None = None
    procedimiento: str | None = None
    cita: str | None = None


# ------------------------------------------------------------------- manuales


class ConsultaManuales(ModeloEstricto):
    """Búsqueda de texto en los manuales."""

    consulta: str = Field(min_length=2)
    documento: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class FragmentoManual(ModeloEstricto):
    """Fragmento de manual con su cita, para poder citarlo en la respuesta."""

    documento: str
    seccion: str
    cita: str
    titulo: str | None = None
    texto: str
    vigente: bool = True
    puntaje: float | None = None


# ------------------------------------------------- envoltorios de salida MCP
#
# El protocolo MCP exige que `structuredContent` sea un **objeto**, no un
# arreglo. Por eso las salidas que son listas van dentro de un campo con
# nombre, y no como `RootModel[list[...]]` (que serializa a un JSON array).


class ListaEquipos(ModeloEstricto):
    """Salida de la tool ``equipos``."""

    equipos: list[Equipo]

    @classmethod
    def envolver(cls, valores: list[Equipo]) -> ListaEquipos:
        return cls(equipos=valores)


class ListaOrdenes(ModeloEstricto):
    """Salida de la tool ``historial_ordenes``."""

    ordenes: list[OrdenHistorico]

    @classmethod
    def envolver(cls, valores: list[OrdenHistorico]) -> ListaOrdenes:
        return cls(ordenes=valores)


class ListaRepuestos(ModeloEstricto):
    """Salida de la tool ``inventario``."""

    repuestos: list[RepuestoStock]

    @classmethod
    def envolver(cls, valores: list[RepuestoStock]) -> ListaRepuestos:
        return cls(repuestos=valores)


class ListaMateriales(ModeloEstricto):
    """Salida de la tool ``lista_materiales``."""

    materiales: list[MaterialEquipo]

    @classmethod
    def envolver(cls, valores: list[MaterialEquipo]) -> ListaMateriales:
        return cls(materiales=valores)


class ListaTareas(ModeloEstricto):
    """Salida de la tool ``catalogo_tareas``."""

    tareas: list[TareaCatalogo]

    @classmethod
    def envolver(cls, valores: list[TareaCatalogo]) -> ListaTareas:
        return cls(tareas=valores)


class ListaFragmentos(ModeloEstricto):
    """Salida de la tool ``busqueda_manuales``."""

    fragmentos: list[FragmentoManual]

    @classmethod
    def envolver(cls, valores: list[FragmentoManual]) -> ListaFragmentos:
        return cls(fragmentos=valores)
