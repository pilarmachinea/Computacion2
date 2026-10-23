"""Primera etapa del TP: API, cola y tareas de espera en memoria."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


class PedidoEspera(BaseModel):
    # FastAPI valida el JSON antes de ejecutar el endpoint.
    segundos: float = Field(ge=0, le=3600, allow_inf_nan=False)


Estado = Literal["pendiente", "ejecutando", "completada", "fallida", "cancelada"]


async def ejecutar_espera(trabajo):
    await asyncio.sleep(trabajo["segundos"])
    return f"Esperó {trabajo['segundos']} segundos"


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
            ejecucion = asyncio.create_task(ejecutar_espera(trabajo))
            ejecuciones[identificador] = ejecucion
            trabajo["resultado"] = await ejecucion
            trabajo["estado"] = "completada"
        except asyncio.CancelledError:
            trabajo["estado"] = "cancelada"
            # Si cancelaron al worker (apagado), debe salir del bucle.
            # Si cancelaron solamente su trabajo (DELETE), sigue disponible.
            if asyncio.current_task().cancelling():
                raise
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
async def crear_tarea(pedido: PedidoEspera, request: Request):
    identificador = str(uuid4())
    request.app.state.tareas[identificador] = {
        "id": identificador,
        "tipo": "esperar",
        "segundos": pedido.segundos,
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
