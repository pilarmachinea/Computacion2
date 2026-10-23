"""Primera etapa del TP: API, cola y tareas de espera en memoria."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


class PedidoEspera(BaseModel):
    # FastAPI valida el JSON antes de ejecutar el endpoint.
    segundos: float = Field(ge=0, le=3600, allow_inf_nan=False)


async def worker(cola, tareas):
    while True:
        identificador = await cola.get()
        trabajo = tareas[identificador]
        try:
            trabajo["estado"] = "ejecutando"
            await asyncio.sleep(trabajo["segundos"])
            trabajo["resultado"] = f"Esperó {trabajo['segundos']} segundos"
            trabajo["estado"] = "completada"
        except asyncio.CancelledError:
            trabajo["estado"] = "cancelada"
            raise
        except Exception as error:
            trabajo["estado"] = "fallida"
            trabajo["error"] = str(error)
        finally:
            cola.task_done()


@asynccontextmanager
async def lifespan(app):
    # Estas estructuras se crean una vez al iniciar la aplicación.
    app.state.cola = asyncio.Queue()
    app.state.tareas = {}
    workers = [
        asyncio.create_task(worker(app.state.cola, app.state.tareas))
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
    }
    await request.app.state.cola.put(identificador)
    return {"id": identificador, "estado": "pendiente"}


@app.get("/tareas/{identificador}")
async def consultar_tarea(identificador: str, request: Request):
    trabajo = request.app.state.tareas.get(identificador)
    if trabajo is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return trabajo.copy()
