import time
import multiprocessing as mp

from recolector import recolector_loop
from analizadores.resumen import analizador_resumen_loop
from analizadores.memoria import analizador_memoria_loop
from analizadores.fds import analizador_fds_loop


def main():
    manager = mp.Manager()
    snapshot = manager.dict()
    vista_resumen = manager.list()
    vista_memoria = manager.list()
    vista_fds = manager.list()

    procesos = [
        mp.Process(target=recolector_loop, args=(snapshot, 1), daemon=True),
        mp.Process(target=analizador_resumen_loop, args=(snapshot, vista_resumen, 2), daemon=True),
        mp.Process(target=analizador_memoria_loop, args=(vista_memoria, 2), daemon=True),
        mp.Process(target=analizador_fds_loop, args=(vista_fds, 3), daemon=True),
    ]

    for p in procesos:
        p.start()

    time.sleep(6)

    print("=== Top 3 por RSS (Memoria) ===")
    for fila in vista_memoria[:3]:
        print(f"  PID {fila['pid']:>6}  {fila['comm']:<15}  RSS {fila['vmrss_kb']:>8} KB")

    print("\n=== Top 3 por cantidad de FDs ===")
    for fila in vista_fds[:3]:
        print(f"  PID {fila['pid']:>6}  cantidad_fds={fila['cantidad_fds']}")

    for p in procesos:
        p.terminate()
    for p in procesos:
        p.join()


if __name__ == "__main__":
    main()