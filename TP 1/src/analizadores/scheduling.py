import time
from procfs import listar_pids, scheduling_de


def analizador_scheduling_loop(vista_compartida, intervalo=3):
    while True:
        filas = []
        for pid in listar_pids():
            datos = scheduling_de(pid)
            if datos is not None:
                filas.append(datos)

        filas_ordenadas = sorted(filas, key=lambda p: p["nonvoluntary_ctxt_switches"], reverse=True)
        vista_compartida[:] = filas_ordenadas
        time.sleep(intervalo)