import time
from procfs import listar_pids, leer_fds, leer_stat
from procesos import restaurar_senales_hijo


def analizador_fds_loop(vista_compartida, intervalo):
    restaurar_senales_hijo()
    while True:
        filas = []

        for pid in listar_pids():
            datos = leer_fds(pid)
            if datos is not None:
                stat = leer_stat(pid)
                filas.append({
                    "pid": pid,
                    "comm": stat["comm"] if stat else "",
                    "cantidad_fds": len(datos),
                    "fds": datos,
                })

        filas.sort(key=lambda p: p["cantidad_fds"], reverse=True)
        vista_compartida[:] = filas

        time.sleep(intervalo.value)