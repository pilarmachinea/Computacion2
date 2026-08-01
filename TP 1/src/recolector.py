import time
from procfs import listar_pids, resumen_de
from procesos import restaurar_senales_hijo
from cpu import CalculadoraCPU


def recolector_loop(snapshot_compartido, config):
    restaurar_senales_hijo()
    calc_cpu = CalculadoraCPU()

    while True:
        pids = listar_pids()
        nuevo_snapshot = {}

        for pid in pids:
            datos = resumen_de(pid)
            if datos is None:
                continue

            datos["cpu_pct"] = calc_cpu.calcular(
                pid,
                datos["utime"],
                datos["stime"]
            )

            nuevo_snapshot[pid] = datos

        snapshot_compartido.update(nuevo_snapshot)

        intervalo = config["intervalos"].get("recolector", 1)
        time.sleep(intervalo)