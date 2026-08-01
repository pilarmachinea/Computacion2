import time
from procesos import restaurar_senales_hijo

def analizador_resumen_loop(snapshot_recolector, vista_compartida, config):
    restaurar_senales_hijo()
    while True:
        filas = list(snapshot_recolector.values())
        filas_ordenadas = sorted(
            filas,
            key=lambda p: p["cpu_pct"],
            reverse=True
        )

        vista_compartida[:] = filas_ordenadas

        intervalo = config["intervalos"].get("resumen", 2)
        time.sleep(intervalo)