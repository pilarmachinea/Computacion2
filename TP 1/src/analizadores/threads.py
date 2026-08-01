import time
from procfs import listar_pids, leer_threads
from procesos import restaurar_senales_hijo


def analizador_threads_loop(vista_compartida, intervalo):
    restaurar_senales_hijo()
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

        time.sleep(intervalo.value)