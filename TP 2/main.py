"""API de tareas concurrentes con estado en memoria y volcado al apagar."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import multiprocessing
import signal
from concurrent.futures import ProcessPoolExecutor
import os
import json
import logging
import math
from pathlib import Path
from datetime import datetime, timezone
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


async def ejecutar_descarga(trabajo, semaforo):
    async def acotada(cliente, url):
        async with semaforo:
            return await descargar_url(cliente, url)

    # Un cliente compartido por las URLs del trabajo; se cierra al salir.
    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as cliente:
        async with asyncio.TaskGroup() as grupo:
            descargas = [
                grupo.create_task(acotada(cliente, url))
                for url in trabajo["urls"]
            ]
        # TaskGroup espera todas y cancela sus hijas si se cancela el trabajo.
        return [descarga.result() for descarga in descargas]


def calcular_hash(texto, iteraciones, detener):
    """Función síncrona: se ejecuta en un proceso, fuera del event loop."""
    datos = texto.encode("utf-8")
    for numero in range(iteraciones):
        # Cancelación cooperativa: un proceso no se detiene con Future.cancel().
        if numero % 1000 == 0 and detener.is_set():
            return None
        datos = hashlib.sha256(datos).digest()
    return datos.hex()


# Los eventos se comparten al crear los procesos con spawn, no por cada pedido.
_senales_proceso = None


def iniciar_proceso(senales):
    global _senales_proceso
    # Ctrl+C lo coordina Uvicorn; los hijos paran con sus eventos compartidos.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _senales_proceso = senales


def calcular_hash_proceso(texto, iteraciones, indice, marca=None):
    if marca is not None:  # Solo lo usa el benchmark para sincronizar su cliente.
        Path(marca).write_text("inicio")
    return calcular_hash(texto, iteraciones, _senales_proceso[indice])


async def ejecutar_hash(trabajo, recursos, indice):
    # Esperar capacidad en asyncio permite cancelar sin quedar en la cola del executor.
    async with recursos.semaforo_cpu:
        return await ejecutar_hash_en_proceso(trabajo, recursos, indice)


async def ejecutar_hash_en_proceso(trabajo, recursos, indice):
    detener = recursos.senales[indice]
    detener.clear()
    calculo = asyncio.get_running_loop().run_in_executor(
        recursos.pool_cpu, calcular_hash_proceso,
        trabajo["texto"], trabajo["iteraciones"], indice, trabajo.get("_marca"),
    )
    try:
        resultado = await asyncio.shield(calculo)
        return {"algoritmo": "sha256", "hash": resultado,
                "iteraciones": trabajo["iteraciones"]}
    except asyncio.CancelledError:
        detener.set()
        # No reutilizar el evento hasta que el proceso haya salido del cálculo.
        while not calculo.done():
            try:
                await asyncio.shield(calculo)
            except asyncio.CancelledError:
                continue
        calculo.result()
        raise


async def ejecutar_trabajo(trabajo, recursos, indice):
    if trabajo["tipo"] == "hashear":
        return await ejecutar_hash(trabajo, recursos, indice)
    if trabajo["tipo"] == "descargar":
        return await ejecutar_descarga(trabajo, recursos.semaforo_descargas)
    return await ejecutar_espera(trabajo)


async def worker(cola, tareas, ejecuciones, recursos, indice):
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
            ejecucion = asyncio.create_task(ejecutar_trabajo(trabajo, recursos, indice))
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


logger = logging.getLogger("uvicorn.error")


def guardar_estado(directorio, tareas):
    directorio.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now(timezone.utc)
    destino = directorio / f"estado_{fecha.strftime('%Y%m%dT%H%M%S_%fZ')}.json"
    temporal = destino.with_suffix(".tmp")
    temporal.write_text(json.dumps(
        {"guardado_en": fecha.isoformat(), "tareas": tareas},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")
    temporal.replace(destino)
    return destino


@asynccontextmanager
async def lifespan(app):
    cantidad = int(os.getenv("WORKERS", "3"))
    cola_max = int(os.getenv("COLA_MAX", "100"))
    descargas_max = int(os.getenv("DESCARGAS_MAX", "20"))
    cpu_max = int(os.getenv("CPU_PROCESOS", "2"))
    plazo = float(os.getenv("APAGADO_TIMEOUT", "10"))
    if min(cantidad, cola_max, descargas_max, cpu_max) < 1 or not math.isfinite(plazo) or plazo < 0:
        raise ValueError("Los límites deben ser positivos y APAGADO_TIMEOUT finito y >= 0")
    directorio = Path(os.getenv("ESTADO_DIR", "datos"))
    anteriores = await asyncio.to_thread(lambda: sorted(directorio.glob("estado_*.json")))
    if anteriores:
        logger.info("Volcado previo detectado: %s (no se restaura)", anteriores[-1])
    app.state.cola = asyncio.Queue(maxsize=cola_max)
    app.state.semaforo_descargas = asyncio.Semaphore(descargas_max)
    app.state.descargas_max = descargas_max
    app.state.cpu_procesos = cpu_max
    app.state.semaforo_cpu = asyncio.Semaphore(cpu_max)
    contexto = multiprocessing.get_context("spawn")
    app.state.senales = [contexto.Event() for _ in range(cantidad)]
    app.state.pool_cpu = ProcessPoolExecutor(
        max_workers=cpu_max, mp_context=contexto,
        initializer=iniciar_proceso, initargs=(app.state.senales,),
    )
    app.state.tareas = {}
    app.state.ejecuciones = {}
    app.state.aceptando = True
    app.state.cantidad_workers = cantidad
    workers = [
        asyncio.create_task(worker(
            app.state.cola, app.state.tareas, app.state.ejecuciones, app.state, indice
        ))
        for indice in range(cantidad)
    ]
    logger.info("Pool iniciado con %s workers", cantidad)
    try:
        yield
    finally:
        app.state.aceptando = False
        # No iniciamos pendientes durante el apagado. Conservamos su registro.
        while not app.state.cola.empty():
            identificador = app.state.cola.get_nowait()
            app.state.tareas[identificador]["estado"] = "cancelada"
            app.state.cola.task_done()
        try:
            # Solo quedan por atender las tareas que estaban en ejecución.
            await asyncio.wait_for(app.state.cola.join(), timeout=plazo)
        except TimeoutError:
            logger.info("Plazo de apagado agotado; cancelando trabajos restantes")
        finally:
            for tarea in workers:
                tarea.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        await asyncio.to_thread(app.state.pool_cpu.shutdown, wait=True, cancel_futures=True)
        destino = await asyncio.to_thread(guardar_estado, directorio, app.state.tareas)
        logger.info("Estado guardado en %s", destino)


app = FastAPI(title="TP2 - Plataforma de tareas", lifespan=lifespan)


@app.post("/tareas", status_code=202)
async def crear_tarea(pedido: PedidoEspera | PedidoDescarga | PedidoHash, request: Request):
    if not request.app.state.aceptando:
        raise HTTPException(status_code=503, detail="El servidor se está apagando")
    identificador = str(uuid4())
    trabajo = {
        "id": identificador,
        **pedido.model_dump(mode="json"),
        "estado": "pendiente",
        "resultado": None,
        "error": None,
        "duracion_segundos": None,
    }
    # Sin await entre encolar y registrar: otro worker no puede intercalarse.
    try:
        request.app.state.cola.put_nowait(identificador)
    except asyncio.QueueFull:
        raise HTTPException(
            status_code=503, detail="Cola llena; reintentar más tarde",
            headers={"Retry-After": "1"},
        )
    request.app.state.tareas[identificador] = trabajo
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
        "cola_capacidad": request.app.state.cola.maxsize,
        "cola_ocupada": request.app.state.cola.qsize(),
        "descargas_max": request.app.state.descargas_max,
        "cpu_procesos": request.app.state.cpu_procesos,
        "workers_totales": request.app.state.cantidad_workers,
        "workers_ocupados": len(request.app.state.ejecuciones),
        # Tiempo de ejecución de las completadas; excluye espera en cola.
        "promedio_ejecucion_completadas_segundos": (
            sum(duraciones) / len(duraciones) if duraciones else None
        ),
    }
