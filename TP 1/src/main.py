import time
import multiprocessing as mp

from recolector import recolector_loop
from analizadores.resumen import analizador_resumen_loop


def main():
    manager = mp.Manager()
    snapshot = manager.dict()
    vista_resumen = manager.list()

    p_recolector = mp.Process(
        target=recolector_loop, args=(snapshot, 1), daemon=True
    )
    p_resumen = mp.Process(
        target=analizador_resumen_loop, args=(snapshot, vista_resumen, 2), daemon=True
    )

    p_recolector.start()
    p_resumen.start()

    time.sleep(6)

    print(f"Filas en vista_resumen: {len(vista_resumen)}")
    print("Top 3 por CPU%:")
    for fila in vista_resumen[:3]:
        print(f"  PID {fila['pid']:>6}  {fila['comm']:<20}  CPU {fila['cpu_pct']:.1f}%  usuario={fila['usuario']}")

    p_recolector.terminate()
    p_resumen.terminate()
    p_recolector.join()
    p_resumen.join()


if __name__ == "__main__":
    main()