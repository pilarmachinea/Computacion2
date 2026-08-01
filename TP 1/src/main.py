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
from display import display_loop
from logger_monitor import registrar_evento

RUTA_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config.json")


def cargar_config_desde_archivo():
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
        registrar_evento(f"No se pudo cargar config.json ({e}), usando defaults.")
        return defaults


class ManejadorSenales:
    """Patrón self-pipe: ver notas_teoricas.md sesión 9 para el detalle completo."""
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


def manejar_shutdown(procesos, cola_eventos):
    """
    SIGINT/SIGTERM: termina todos los procesos hijos de forma prolija.
    Este es el único mensaje que SÍ se imprime directo a la terminal con
    print() -- ocurre DESPUÉS de terminar el proceso de display (que es
    el que controla la pantalla con rich.Live), así que para este punto
    ya no hay conflicto de dos procesos escribiendo a la vez.
    """
    registrar_evento("Señal de shutdown recibida. Cerrando procesos...")
    for p in procesos:
        p.terminate()
    for p in procesos:
        p.join()
    print("\n[monitor] Todos los procesos cerrados. Chau.")


def manejar_dump(snapshot, cola_eventos):
    """SIGUSR1: vuelca el snapshot a JSON. El aviso va a la cola de eventos
    (para que display.py lo muestre) y al log -- NUNCA a print() directo,
    porque display.py está controlando la pantalla en ese momento."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"dump_{timestamp}.json"
    copia = dict(snapshot)
    with open(nombre_archivo, "w") as f:
        json.dump(copia, f, indent=2, default=str)

    mensaje = f"Snapshot volcado a {nombre_archivo}"
    registrar_evento(mensaje)
    cola_eventos.put(mensaje)


def manejar_reload_config(intervalos, cola_eventos):
    """SIGHUP: relee config.json y actualiza los Value compartidos."""
    nueva_config = cargar_config_desde_archivo()
    nuevos = nueva_config.get("intervalos", {})
    for nombre, value in intervalos.items():
        if nombre in nuevos:
            value.value = float(nuevos[nombre])

    mensaje = "Configuración recargada desde config.json"
    registrar_evento(mensaje)
    cola_eventos.put(mensaje)


def main():
    manejador = ManejadorSenales()
    modo_verbose = False

    manager = mp.Manager()
    # multiprocessing.Queue (no queue.Queue): acá SÍ cruza la frontera de
    # procesos reales (main.py -> display.py), a diferencia de la cola de
    # teclado de display.py que es interna a un solo proceso (thread).
    cola_eventos = mp.Queue()

    config_inicial = cargar_config_desde_archivo()

    intervalos = {
        "recolector": mp.Value("d", config_inicial["intervalos"]["recolector"]),
        "resumen": mp.Value("d", config_inicial["intervalos"]["resumen"]),
        "memoria": mp.Value("d", config_inicial["intervalos"]["memoria"]),
        "fds": mp.Value("d", config_inicial["intervalos"]["fds"]),
        "threads": mp.Value("d", config_inicial["intervalos"]["threads"]),
        "senales": mp.Value("d", config_inicial["intervalos"]["senales"]),
        "scheduling": mp.Value("d", config_inicial["intervalos"]["scheduling"]),
        "sistema": mp.Value("d", config_inicial["intervalos"]["sistema"]),
    }

    snapshot = manager.dict()
    vista_resumen = manager.list()
    vista_memoria = manager.list()
    vista_fds = manager.list()
    vista_threads = manager.list()
    vista_senales = manager.list()
    vista_scheduling = manager.list()
    vista_sistema = manager.dict()

    vistas_compartidas = {
        "resumen": vista_resumen, "memoria": vista_memoria, "fds": vista_fds,
        "threads": vista_threads, "senales": vista_senales,
        "scheduling": vista_scheduling, "sistema": vista_sistema,
    }

    procesos = [
        mp.Process(target=recolector_loop, args=(snapshot, intervalos["recolector"]), daemon=True),
        mp.Process(target=analizador_resumen_loop, args=(snapshot, vista_resumen, intervalos["resumen"]), daemon=True),
        mp.Process(target=analizador_memoria_loop, args=(vista_memoria, intervalos["memoria"]), daemon=True),
        mp.Process(target=analizador_fds_loop, args=(vista_fds, intervalos["fds"]), daemon=True),
        mp.Process(target=analizador_threads_loop, args=(vista_threads, intervalos["threads"]), daemon=True),
        mp.Process(target=analizador_senales_loop, args=(vista_senales, intervalos["senales"]), daemon=True),
        mp.Process(target=analizador_scheduling_loop, args=(vista_scheduling, intervalos["scheduling"]), daemon=True),
        mp.Process(target=analizador_sistema_loop, args=(vista_sistema, intervalos["sistema"]), daemon=True),
        mp.Process(target=display_loop, args=(vistas_compartidas, intervalos, cola_eventos), daemon=True),
    ]

    for p in procesos:
        p.start()

    registrar_evento(f"Monitor iniciado. PID: {os.getpid()}")

    while True:
        signum = manejador.esperar_senal(timeout=1.0)

        if signum is None:
            continue

        if signum in (signal.SIGINT, signal.SIGTERM):
            manejar_shutdown(procesos, cola_eventos)
            break

        elif signum == signal.SIGUSR1:
            manejar_dump(snapshot, cola_eventos)

        elif signum == signal.SIGUSR2:
            modo_verbose = not modo_verbose
            cola_eventos.put(f"Modo verbose: {'ON' if modo_verbose else 'OFF'}")

        elif signum == signal.SIGHUP:
            manejar_reload_config(intervalos, cola_eventos)


if __name__ == "__main__":
    main()