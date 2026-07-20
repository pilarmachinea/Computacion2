import time
from procfs import listar_pids, leer_fds


def analizador_fds_loop(vista_compartida, intervalo=3):
    while True:
        filas = []
        for pid in listar_pids():
            fds = leer_fds(pid)
            if fds is not None:
                filas.append({"pid": pid, "cantidad_fds": len(fds), "fds": fds})

        filas_ordenadas = sorted(filas, key=lambda p: p["cantidad_fds"], reverse=True)
        vista_compartida[:] = filas_ordenadas
        time.sleep(intervalo)