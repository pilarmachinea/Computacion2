"""Ejemplo de una cola compartida por workers asíncronos."""

import argparse
import asyncio
from time import perf_counter


async def worker(numero, cola):
    while True:
        trabajo = await cola.get()
        try:
            print(f"Worker {numero}: empieza {trabajo}")
            await asyncio.sleep(2)
            print(f"Worker {numero}: termina {trabajo}")
        finally:
            # Avisa a la cola que este elemento ya fue atendido.
            cola.task_done()


async def main(cantidad_workers):
    cola = asyncio.Queue()
    inicio = perf_counter()

    workers = [
        asyncio.create_task(worker(numero, cola))
        for numero in range(1, cantidad_workers + 1)
    ]

    try:
        for nombre in ["A", "B", "C"]:
            await cola.put(nombre)

        # Espera a que cada elemento tenga su correspondiente task_done().
        await cola.join()
    finally:
        # Los workers siguen esperando nuevos trabajos: hay que cerrarlos.
        for tarea in workers:
            tarea.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    print(f"Tiempo total con {cantidad_workers} worker(s): "
          f"{perf_counter() - inicio:.2f} segundos")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1, 4), default=3)
    args = parser.parse_args()
    asyncio.run(main(args.workers))
