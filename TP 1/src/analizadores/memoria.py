import time
from procfs import listar_pids, memoria_de


def analizador_memoria_loop(vista_compartida, config):
    while True:
        filas = []

        for pid in listar_pids():
            datos = memoria_de(pid)
            if datos is not None:
                filas.append(datos)

        filas.sort(key=lambda p: p["vmrss_kb"], reverse=True)
        vista_compartida[:] = filas

        intervalo = config["intervalos"].get("memoria", 3)
        time.sleep(intervalo)