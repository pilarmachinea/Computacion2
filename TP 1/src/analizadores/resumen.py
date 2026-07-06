import time


def analizador_resumen_loop(snapshot_recolector, vista_compartida, intervalo=2):
    """
    snapshot_recolector: Manager().dict() que llena el Recolector (pid -> datos crudos)
    vista_compartida: Manager().list() donde este analizador escribe las filas
                       ya ordenadas y listas para mostrar
    """
    while True:
        filas = list(snapshot_recolector.values())
        filas_ordenadas = sorted(filas, key=lambda p: p["cpu_pct"], reverse=True)

        vista_compartida[:] = filas_ordenadas
        time.sleep(intervalo)