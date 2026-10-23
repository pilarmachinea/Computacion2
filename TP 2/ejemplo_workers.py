"""Ejemplo de una cola compartida por workers asíncronos."""

import argparse
import asyncio
from time import perf_counter


async def worker(numero, cola, tareas):
    while True:
        identificador = await cola.get()
        trabajo = tareas[identificador]
        try:
            trabajo["estado"] = "ejecutando"
            print(f"Worker {numero}: {identificador} → ejecutando")
            await asyncio.sleep(trabajo["segundos"])
            trabajo["resultado"] = f"Esperó {trabajo['segundos']} segundos"
            trabajo["estado"] = "completada"
            print(f"Worker {numero}: {identificador} → completada")
        except asyncio.CancelledError:
            trabajo["estado"] = "cancelada"
            raise
        except Exception as error:
            trabajo["estado"] = "fallida"
            trabajo["error"] = str(error)
            print(f"Worker {numero}: {identificador} → fallida: {error}")
        finally:
            # Avisa a la cola que este elemento ya fue atendido.
            cola.task_done()


async def main(cantidad_workers):
    cola = asyncio.Queue()
    tareas = {}  # Conserva los estados y resultados, incluso al salir de la cola.
    inicio = perf_counter()

    workers = [
        asyncio.create_task(worker(numero, cola, tareas))
        for numero in range(1, cantidad_workers + 1)
    ]

    try:
        for nombre in ["A", "B", "C"]:
            tareas[nombre] = {
                "estado": "pendiente",
                "segundos": 2,
                "resultado": None,
                "error": None,
            }
            # La cola guarda identificadores; el diccionario guarda los datos.
            await cola.put(nombre)
            print(f"Tarea {nombre} → pendiente")

        # Espera a que cada elemento tenga su correspondiente task_done().
        await cola.join()
    finally:
        # Los workers siguen esperando nuevos trabajos: hay que cerrarlos.
        for tarea in workers:
            tarea.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    print("\nEstado final de las tareas:")
    for identificador, trabajo in tareas.items():
        print(f"{identificador}: {trabajo}")

    print(f"Tiempo total con {cantidad_workers} worker(s): "
          f"{perf_counter() - inicio:.2f} segundos")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1, 4), default=3)
    args = parser.parse_args()
    asyncio.run(main(args.workers))
