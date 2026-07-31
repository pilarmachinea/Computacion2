import time
from procfs import listar_pids, leer_threads


def analizador_threads_loop(vista_compartida, config):
    while True:
        filas = []

        for pid in listar_pids():
            datos = leer_threads(pid)
            if datos is not None:
                filas.append({
                    "pid": pid,
                    "cantidad_threads": len(datos),
                    "threads": datos,
                })

        filas.sort(
            key=lambda p: p["cantidad_threads"],
            reverse=True
        )

        vista_compartida[:] = filas

        intervalo = config["intervalos"].get("threads", 2)
        time.sleep(intervalo)