import time
from procfs import listar_pids, scheduling_de
from procesos import restaurar_senales_hijo

def analizador_scheduling_loop(vista_compartida, intervalo):
    restaurar_senales_hijo()
    while True:
        filas = []

        for pid in listar_pids():
            datos = scheduling_de(pid)
            if datos is not None:
                filas.append(datos)

        filas.sort(
            key=lambda p: p["nonvoluntary_ctxt_switches"],
            reverse=True
        )

        vista_compartida[:] = filas

        time.sleep(intervalo.value)