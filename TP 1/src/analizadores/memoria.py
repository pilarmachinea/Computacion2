import time
from procfs import listar_pids, memoria_de
from procesos import restaurar_senales_hijo

def analizador_memoria_loop(vista_compartida, intervalo):
    restaurar_senales_hijo()
    while True:
        filas = []

        for pid in listar_pids():
            datos = memoria_de(pid)
            if datos is not None:
                filas.append(datos)

        filas.sort(key=lambda p: p["vmrss_kb"], reverse=True)
        vista_compartida[:] = filas

        time.sleep(intervalo.value)