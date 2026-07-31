import os
import select
import signal
import json
import multiprocessing as mp
from datetime import datetime

from recolector import recolector_loop
from analizadores.resumen import analizador_resumen_loop
from analizadores.memoria import analizador_memoria_loop
from analizadores.fds import analizador_fds_loop
from analizadores.threads import analizador_threads_loop
from analizadores.senales import analizador_senales_loop
from analizadores.scheduling import analizador_scheduling_loop
from analizadores.sistema import analizador_sistema_loop


class ManejadorSenales:
    """
    Patrón self-pipe: los handlers de señal solo escriben un byte al pipe
    (operación async-signal-safe). El loop principal lee del pipe y decide
    qué hacer, fuera del contexto restringido de un signal handler.
    """
    def __init__(self):
        self._r, self._w = os.pipe()
        os.set_blocking(self._r, False)

        signal.signal(signal.SIGINT, self._handler)
        signal.signal(signal.SIGTERM, self._handler)
        signal.signal(signal.SIGHUP, self._handler)
        signal.signal(signal.SIGUSR1, self._handler)
        signal.signal(signal.SIGUSR2, self._handler)

    def _handler(self, signum, frame):
        os.write(self._w, bytes([signum]))

    def esperar_senal(self, timeout=1.0):
        listos, _, _ = select.select([self._r], [], [], timeout)
        if not listos:
            return None
        datos = os.read(self._r, 1)
        return datos[0]


def manejar_shutdown(procesos):
    """SIGINT/SIGTERM: termina todos los procesos hijos de forma prolija."""
    print("\n[monitor] Señal de shutdown recibida. Cerrando procesos...")
    for p in procesos:
        p.terminate()
    for p in procesos:
        p.join()
    print("[monitor] Todos los procesos cerrados. Chau.")


def manejar_dump(snapshot):
    """SIGUSR1: vuelca el snapshot actual a un JSON con timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"dump_{timestamp}.json"
    copia = dict(snapshot)
    with open(nombre_archivo, "w") as f:
        json.dump(copia, f, indent=2, default=str)
    print(f"\n[monitor] Snapshot volcado a {nombre_archivo}")


def manejar_reload_config():
    """SIGHUP: recarga config.json (intervalos por vista, filtros default)."""
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        print(f"\n[monitor] Configuración recargada: {config}")
        return config
    except FileNotFoundError:
        print("\n[monitor] config.json no encontrado, se ignora el reload.")
        return None


def main():
    manejador = ManejadorSenales()
    modo_verbose = False  # toggled por SIGUSR2

    manager = mp.Manager()
    snapshot = manager.dict()
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

    print("[monitor] Corriendo. PID:", os.getpid())
    print("  Probá en otra terminal:")
    print(f"    kill -USR1 {os.getpid()}   # dump del snapshot")
    print(f"    kill -USR2 {os.getpid()}   # toggle modo verbose")
    print(f"    kill -HUP  {os.getpid()}   # recargar config.json")
    print(f"    kill -TERM {os.getpid()}   # shutdown limpio (o Ctrl+C)")

    while True:
        signum = manejador.esperar_senal(timeout=1.0)

        if signum is None:
            continue  # timeout: no llegó nada, seguir el loop normalmente

        if signum in (signal.SIGINT, signal.SIGTERM):
            manejar_shutdown(procesos)
            break

        elif signum == signal.SIGUSR1:
            manejar_dump(snapshot)

        elif signum == signal.SIGUSR2:
            modo_verbose = not modo_verbose
            print(f"\n[monitor] Modo verbose: {'ON' if modo_verbose else 'OFF'}")

        elif signum == signal.SIGHUP:
            manejar_reload_config()


if __name__ == "__main__":
    main()