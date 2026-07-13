import time
from procfs import listar_pids, memoria_de


def analizador_memoria_loop(vista_compartida, intervalo=2):
    """
    A diferencia del analizador de Resumen, este NO lee del snapshot
    del Recolector: lee /proc directamente, porque necesita datos
    (maps) que el Recolector no junta.
    """
    while True:
        filas = []
        for pid in listar_pids():
            datos = memoria_de(pid)
            if datos is not None:
                filas.append(datos)

        filas_ordenadas = sorted(filas, key=lambda p: p["vmrss_kb"], reverse=True)
        vista_compartida[:] = filas_ordenadas
        time.sleep(intervalo)