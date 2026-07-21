import time
from procfs import listar_pids, leer_threads


def analizador_threads_loop(vista_compartida, intervalo=3):
    while True:
        filas = []
        for pid in listar_pids():
            threads = leer_threads(pid)
            if threads:
                filas.append({"pid": pid, "cantidad_threads": len(threads), "threads": threads})

        filas_ordenadas = sorted(filas, key=lambda p: p["cantidad_threads"], reverse=True)
        vista_compartida[:] = filas_ordenadas
        time.sleep(intervalo)