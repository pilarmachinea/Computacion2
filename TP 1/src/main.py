import time
import multiprocessing as mp

from recolector import recolector_loop
from analizadores.resumen import analizador_resumen_loop
from analizadores.memoria import analizador_memoria_loop
from analizadores.fds import analizador_fds_loop
from analizadores.threads import analizador_threads_loop
from analizadores.senales import analizador_senales_loop
from analizadores.scheduling import analizador_scheduling_loop
from analizadores.sistema import analizador_sistema_loop


def main():
    manager = mp.Manager()

    # Snapshot base del Recolector
    snapshot = manager.dict()

    # Vistas: una por cada analizador. Todas son Manager().list() (una fila
    # por proceso) salvo Sistema, que es Manager().dict() (un único estado
    # global, no una lista de procesos).
    vista_resumen = manager.list()
    vista_memoria = manager.list()
    vista_fds = manager.list()
    vista_threads = manager.list()
    vista_senales = manager.list()
    vista_scheduling = manager.list()
    vista_sistema = manager.dict()

    procesos = [
        mp.Process(target=recolector_loop, args=(snapshot, 1), daemon=True),
        mp.Process(target=analizador_resumen_loop, args=(snapshot, vista_resumen, 2), daemon=True),
        mp.Process(target=analizador_memoria_loop, args=(vista_memoria, 3), daemon=True),
        mp.Process(target=analizador_fds_loop, args=(vista_fds, 5), daemon=True),
        mp.Process(target=analizador_threads_loop, args=(vista_threads, 2), daemon=True),
        mp.Process(target=analizador_senales_loop, args=(vista_senales, 10), daemon=True),
        mp.Process(target=analizador_scheduling_loop, args=(vista_scheduling, 3), daemon=True),
        mp.Process(target=analizador_sistema_loop, args=(vista_sistema, 2), daemon=True),
    ]

    for p in procesos:
        p.start()

    time.sleep(8)  # dejamos correr un poco más para que Sistema tenga 2da lectura de CPU

    print("=== Sistema ===")
    print(f"  CPU: user={vista_sistema['cpu_pct']['user']:.1f}%  "
          f"system={vista_sistema['cpu_pct']['system']:.1f}%  "
          f"idle={vista_sistema['cpu_pct']['idle']:.1f}%")
    print(f"  Load average: {vista_sistema['loadavg']}")
    print(f"  Procesos: {vista_sistema['procesos']['total_procesos']} totales, "
          f"{vista_sistema['procesos']['zombies']} zombies, "
          f"{vista_sistema['procesos']['total_threads']} threads")

    print("\n=== Top 3 por RSS (Memoria) ===")
    for fila in vista_memoria[:3]:
        print(f"  PID {fila['pid']:>6}  {fila['comm']:<15}  RSS {fila['vmrss_kb']:>8} KB")

    print("\n=== Top 3 por cantidad de FDs ===")
    for fila in vista_fds[:3]:
        print(f"  PID {fila['pid']:>6}  cantidad_fds={fila['cantidad_fds']}")

    print("\n=== Top 3 por cantidad de threads ===")
    for fila in vista_threads[:3]:
        print(f"  PID {fila['pid']:>6}  cantidad_threads={fila['cantidad_threads']}")

    print("\n=== Top 3 por context switches involuntarios (Scheduling) ===")
    for fila in vista_scheduling[:3]:
        print(f"  PID {fila['pid']:>6}  {fila['comm']:<15}  policy={fila['policy_nombre']:<12}  "
              f"nonvol_cs={fila['nonvoluntary_ctxt_switches']}")

    for p in procesos:
        p.terminate()
    for p in procesos:
        p.join()


if __name__ == "__main__":
    main()