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

# config.json vive en la RAÍZ del repo, un nivel arriba de src/
RUTA_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config.json")

def cargar_config_desde_archivo():
    """Lee config.json. Si no existe o está mal formado, usa defaults razonables."""
    defaults = {
        "intervalos": {
            "recolector": 1, "resumen": 2, "memoria": 3, "fds": 5,
            "threads": 2, "senales": 10, "scheduling": 3, "sistema": 2,
        },
        "filtros_default": {"usuario": None, "estado_minimo_interes": None},
    }
    try:
        with open(RUTA_CONFIG, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[monitor] No se pudo cargar config.json ({e}), usando defaults.")
        return defaults


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


def manejar_reload_config(config_compartido):
    """
    SIGHUP: relee config.json y actualiza el Manager().dict() compartido.
    Como todos los analizadores leen config["intervalos"][...] EN CADA
    VUELTA de su propio loop (no una sola vez al arrancar), el cambio se
    aplica solo con esto -- no hace falta reiniciar ningún proceso.
    """
    nueva_config = cargar_config_desde_archivo()
    config_compartido.clear()
    config_compartido.update(nueva_config)
    print(f"\n[monitor] Configuración recargada: {dict(config_compartido)}")


def main():
    manejador = ManejadorSenales()
    modo_verbose = False  # toggled por SIGUSR2

    manager = mp.Manager()

    config_inicial = cargar_config_desde_archivo()
    config = manager.dict(config_inicial)
    # Nota: config["intervalos"] es un dict normal (no proxy anidado) porque
    # Manager().dict() no crea proxies recursivos para sub-dicts al construir
    # con un dict inicial -- para leerlo alcanza, pero para MODIFICAR solo
    # ese sub-dict habría que reasignar la clave completa (config["intervalos"] = {...}),
    # que es justo lo que hace manejar_reload_config con .clear()+.update().

    snapshot = manager.dict()
    vista_resumen = manager.list()
    vista_memoria = manager.list()
    vista_fds = manager.list()
    vista_threads = manager.list()
    vista_senales = manager.list()
    vista_scheduling = manager.list()
    vista_sistema = manager.dict()

    procesos = [
        mp.Process(target=recolector_loop, args=(snapshot, config), daemon=True),
        mp.Process(target=analizador_resumen_loop, args=(snapshot, vista_resumen, config), daemon=True),
        mp.Process(target=analizador_memoria_loop, args=(vista_memoria, config), daemon=True),
        mp.Process(target=analizador_fds_loop, args=(vista_fds, config), daemon=True),
        mp.Process(target=analizador_threads_loop, args=(vista_threads, config), daemon=True),
        mp.Process(target=analizador_senales_loop, args=(vista_senales, config), daemon=True),
        mp.Process(target=analizador_scheduling_loop, args=(vista_scheduling, config), daemon=True),
        mp.Process(target=analizador_sistema_loop, args=(vista_sistema, config), daemon=True),
    ]

    for p in procesos:
        p.start()

    print("[monitor] Corriendo. PID:", os.getpid())
    print("  Probá en otra terminal:")
    print(f"    kill -USR1 {os.getpid()}   # dump del snapshot")
    print(f"    kill -USR2 {os.getpid()}   # toggle modo verbose")
    print(f"    kill -HUP  {os.getpid()}   # recargar config.json (intervalos en caliente)")
    print(f"    kill -TERM {os.getpid()}   # shutdown limpio (o Ctrl+C)")

    while True:
        signum = manejador.esperar_senal(timeout=1.0)

        if signum is None:
            continue

        if signum in (signal.SIGINT, signal.SIGTERM):
            manejar_shutdown(procesos)
            break

        elif signum == signal.SIGUSR1:
            manejar_dump(snapshot)

        elif signum == signal.SIGUSR2:
            modo_verbose = not modo_verbose
            print(f"\n[monitor] Modo verbose: {'ON' if modo_verbose else 'OFF'}")

        elif signum == signal.SIGHUP:
            manejar_reload_config(config)


if __name__ == "__main__":
    main()