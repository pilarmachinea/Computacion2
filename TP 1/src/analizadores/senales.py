import time
from procfs import listar_pids, senales_de


def analizador_senales_loop(vista_compartida, intervalo=3):
    while True:
        filas = []
        for pid in listar_pids():
            datos = senales_de(pid)
            if datos is not None:
                filas.append(datos)

        vista_compartida[:] = filas
        time.sleep(intervalo)