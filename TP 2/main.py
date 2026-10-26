"""Primera etapa del TP: API, cola y tareas de espera en memoria."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import threading
from uuid import uuid4
from time import perf_counter
from typing import Literal

import httpx

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl


class PedidoEspera(BaseModel):
    tipo: Literal["esperar"] = "esperar"
    # FastAPI valida el JSON antes de ejecutar el endpoint.
    segundos: float = Field(ge=0, le=3600, allow_inf_nan=False)
    timeout: float = Field(default=30, gt=0, le=3600, allow_inf_nan=False)


class PedidoDescarga(BaseModel):
    tipo: Literal["descargar"]
    urls: list[HttpUrl] = Field(min_length=1, max_length=20)
    timeout: float = Field(default=30, gt=0, le=3600, allow_inf_nan=False)


class PedidoHash(BaseModel):
    tipo: Literal["hashear"]
    texto: str = Field(max_length=100_000)
    iteraciones: int = Field(ge=1, le=100_000_000, strict=True)
    timeout: float = Field(default=30, gt=0, le=3600, allow_inf_nan=False)


Estado = Literal["pendiente", "ejecutando", "completada", "fallida", "cancelada"]


async def ejecutar_espera(trabajo):
    await asyncio.sleep(trabajo["segundos"])
    return f"Esperó {trabajo['segundos']} segundos"


async def descargar_url(cliente, url):
    inicio = perf_counter()
    resultado = {
        "url": url, "codigo_estado": None, "tamano_bytes": 0, "error": None,
    }
    try:
        # Lee por bloques y cierra la respuesta incluso si se cancela.
        async with cliente.stream("GET", url) as respuesta:
            resultado["codigo_estado"] = respuesta.status_code
            async for bloque in respuesta.aiter_bytes():
                resultado["tamano_bytes"] += len(bloque)
    except httpx.HTTPError as error:
        resultado["error"] = f"{type(error).__name__}: {error}"
    resultado["tiempo_segundos"] = perf_counter() - inicio
    return resultado


async def ejecutar_descarga(trabajo):
    # Un cliente compartido por las URLs del trabajo; se cierra al salir.
    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as cliente:
        async with asyncio.TaskGroup() as grupo:
            descargas = [
                grupo.create_task(descargar_url(cliente, url))
                for url in trabajo["urls"]
            ]
        # TaskGroup espera todas y cancela sus hijas si se cancela el trabajo.
        return [descarga.result() for descarga in descargas]


def calcular_hash(texto, iteraciones, detener):
    """Función síncrona: se ejecuta en un hilo, fuera del event loop."""
    datos = texto.encode("utf-8")
    for numero in range(iteraciones):
        # Cancelación cooperativa: un hilo no se detiene con Task.cancel().
        if numero % 1000 == 0 and detener.is_set():
            return None
        datos = hashlib.sha256(datos).digest()
    return datos.hex()


async def ejecutar_hash(trabajo):
    detener = threading.Event()
    calculo = asyncio.create_task(asyncio.to_thread(
        calcular_hash, trabajo["texto"], trabajo["iteraciones"], detener
    ))
    try:
        # shield conserva la referencia esperable al cálculo durante la limpieza.
        resultado = await asyncio.shield(calculo)
        return {"algoritmo": "sha256", "hash": resultado,
                "iteraciones": trabajo["iteraciones"]}
    except asyncio.CancelledError:
        detener.set()
        # Esperamos que el cálculo salga, incluso ante cancelaciones repetidas.
        while not calculo.done():
            try:
                await asyncio.shield(calculo)
            except asyncio.CancelledError:
                continue
        calculo.result()
        raise


async def ejecutar_trabajo(trabajo):
    if trabajo["tipo"] == "hashear":
        return await ejecutar_hash(trabajo)
    if trabajo["tipo"] == "descargar":
        return await ejecutar_descarga(trabajo)
    return await ejecutar_espera(trabajo)


async def worker(cola, tareas, ejecuciones):
    while True:
        identificador = await cola.get()
        trabajo = tareas[identificador]
        inicio = None
        try:
            if trabajo["estado"] == "cancelada":
                continue
            inicio = perf_counter()
            trabajo["estado"] = "ejecutando"
            # Se cancela esta ejecución individual, sin destruir el worker.
            ejecucion = asyncio.create_task(ejecutar_trabajo(trabajo))
            ejecuciones[identificador] = ejecucion
            # El límite cuenta desde que empieza la ejecución, no en la cola.
            # Al vencer, cancela y espera la limpieza de la ejecución hija.
            async with asyncio.timeout(trabajo["timeout"]):
                trabajo["resultado"] = await ejecucion
            trabajo["estado"] = "completada"
        except asyncio.CancelledError:
            trabajo["estado"] = "cancelada"
            # Si cancelaron al worker (apagado), debe salir del bucle.
            # Si cancelaron solamente su trabajo (DELETE), sigue disponible.
            if asyncio.current_task().cancelling():
                raise
        except TimeoutError:
            trabajo["estado"] = "fallida"
            trabajo["error"] = (
                f"La tarea superó el timeout de {trabajo['timeout']} segundos"
            )
        except Exception as error:
            trabajo["estado"] = "fallida"
            trabajo["error"] = str(error)
        finally:
            ejecuciones.pop(identificador, None)
            if inicio is not None:
                trabajo["duracion_segundos"] = perf_counter() - inicio
            cola.task_done()


@asynccontextmanager
async def lifespan(app):
    # Estas estructuras se crean una vez al iniciar la aplicación.
    app.state.cola = asyncio.Queue()
    app.state.tareas = {}
    app.state.ejecuciones = {}
    workers = [
        asyncio.create_task(worker(app.state.cola, app.state.tareas, app.state.ejecuciones))
        for _ in range(3)
    ]
    try:
        yield  # Durante esta etapa la API atiende pedidos.
    finally:
        for tarea in workers:
            tarea.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        # En esta primera versión, el cierre cancela también lo pendiente.
        while not app.state.cola.empty():
            identificador = app.state.cola.get_nowait()
            app.state.tareas[identificador]["estado"] = "cancelada"
            app.state.cola.task_done()


app = FastAPI(title="TP2 - Plataforma de tareas", lifespan=lifespan)


@app.post("/tareas", status_code=202)
async def crear_tarea(pedido: PedidoEspera | PedidoDescarga | PedidoHash, request: Request):
    identificador = str(uuid4())
    request.app.state.tareas[identificador] = {
        "id": identificador,
        **pedido.model_dump(mode="json"),
        "estado": "pendiente",
        "resultado": None,
        "error": None,
        "duracion_segundos": None,
    }
    await request.app.state.cola.put(identificador)
    return {"id": identificador, "estado": "pendiente"}


@app.get("/tareas/{identificador}")
async def consultar_tarea(identificador: str, request: Request):
    trabajo = request.app.state.tareas.get(identificador)
    if trabajo is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return trabajo.copy()


@app.get("/tareas")
async def listar_tareas(request: Request, estado: Estado | None = None):
    return [
        trabajo.copy()
        for trabajo in request.app.state.tareas.values()
        if estado is None or trabajo["estado"] == estado
    ]


@app.delete("/tareas/{identificador}")
async def cancelar_tarea(identificador: str, request: Request):
    trabajo = request.app.state.tareas.get(identificador)
    if trabajo is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    if trabajo["estado"] in ("completada", "fallida"):
        raise HTTPException(status_code=409, detail="La tarea ya terminó")
    if trabajo["estado"] == "cancelada":
        return trabajo.copy()

    ejecucion = request.app.state.ejecuciones.get(identificador)
    if ejecucion is None:
        # El identificador queda en la cola; el worker lo salteará al tomarlo.
        trabajo["estado"] = "cancelada"
    else:
        ejecucion.cancel()
        # Espera la limpieza de la ejecución antes de responder.
        await asyncio.gather(ejecucion, return_exceptions=True)
    return trabajo.copy()


@app.get("/estadisticas")
async def estadisticas(request: Request):
    totales = dict.fromkeys(
        ("pendiente", "ejecutando", "completada", "fallida", "cancelada"), 0
    )
    duraciones = []
    for trabajo in request.app.state.tareas.values():
        totales[trabajo["estado"]] += 1
        if trabajo["estado"] == "completada":
            duraciones.append(trabajo["duracion_segundos"])
    return {
        "total": sum(totales.values()),
        "por_estado": totales,
        "workers_ocupados": len(request.app.state.ejecuciones),
        # Tiempo de ejecución de las completadas; excluye espera en cola.
        "promedio_ejecucion_completadas_segundos": (
            sum(duraciones) / len(duraciones) if duraciones else None
        ),
    }
