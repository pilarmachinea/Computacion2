import time
from procfs import listar_pids, senales_de

def analizador_senales_loop(vista_compartida, config):
    while True:
        filas = []

        for pid in listar_pids():
            datos = senales_de(pid)
            if datos is not None:
                filas.append(datos)

        filas.sort(key=lambda p: p["pid"])

        vista_compartida[:] = filas

        intervalo = config["intervalos"].get("senales", 10)
        time.sleep(intervalo)