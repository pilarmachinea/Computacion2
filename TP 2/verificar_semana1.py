"""Comprobación local de API, cola y workers, sin usar el servidor abierto."""

import asyncio
import httpx
from main import app


async def verificar():
    async with app.router.lifespan_context(app):
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://prueba") as cliente:
            ids = []
            for _ in range(4):
                respuesta = await cliente.post(
                    "/tareas", json={"segundos": 60, "timeout": 120}
                )
                assert respuesta.status_code == 202
                assert respuesta.json()["estado"] == "pendiente"
                ids.append(respuesta.json()["id"])

            # Da oportunidad a los workers de tomar los trabajos encolados.
            await asyncio.sleep(0.01)
            datos = (await cliente.get("/estadisticas")).json()
            assert datos["workers_ocupados"] == 3
            assert datos["por_estado"]["pendiente"] == 1
            assert len((await cliente.get("/tareas")).json()) == 4
            pendientes = (await cliente.get("/tareas?estado=pendiente")).json()
            assert pendientes[0]["id"] == ids[3]

            # Cancela primero la pendiente y luego las tres en ejecución.
            for identificador in reversed(ids):
                respuesta = await cliente.delete(f"/tareas/{identificador}")
                assert respuesta.status_code == 200
                assert respuesta.json()["estado"] == "cancelada"
                consulta = await cliente.get(f"/tareas/{identificador}")
                assert consulta.json()["estado"] == "cancelada"

            # Los workers deben seguir disponibles después de cancelar.
            respuesta = await cliente.post("/tareas", json={"segundos": 0.01})
            identificador = respuesta.json()["id"]
            await asyncio.wait_for(app.state.cola.join(), timeout=2)
            consulta = await cliente.get(f"/tareas/{identificador}")
            assert consulta.json()["estado"] == "completada"
            assert (await cliente.get("/tareas/inexistente")).status_code == 404
            assert (await cliente.post("/tareas", json={"segundos": -1})).status_code == 422
    print("Semana 1 verificada: cinco rutas, cola, tres workers y reutilización.")


if __name__ == "__main__":
    asyncio.run(verificar())
