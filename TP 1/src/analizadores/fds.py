import time
from procfs import listar_pids, leer_fds
from procesos import restaurar_senales_hijo

def analizador_fds_loop(vista_compartida, config):
    restaurar_senales_hijo()
    while True:
        filas = []

        for pid in listar_pids():
            datos = leer_fds(pid)
            if datos is not None:
                filas.append({
                    "pid": pid,
                    "cantidad_fds": len(datos),
                    "fds": datos,
                })

        filas.sort(
            key=lambda p: p["cantidad_fds"],
            reverse=True
        )

        vista_compartida[:] = filas

        intervalo = config["intervalos"].get("fds", 5)
        time.sleep(intervalo)