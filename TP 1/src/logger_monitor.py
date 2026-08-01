"""
logger_monitor.py — registro persistente de eventos del monitor a un archivo.

Separado de los prints a terminal: los eventos van tanto a monitor.log
(registro permanente, para debugging/revisión posterior) como a la cola de
eventos que consume display.py (para mostrarlos en la interfaz sin que
main.py escriba directamente sobre la pantalla que rich.Live controla).
"""

import os
from datetime import datetime

RUTA_LOG = os.path.join(os.path.dirname(__file__), "..", "monitor.log")


def registrar_evento(mensaje):
    """Agrega una línea con timestamp a monitor.log (append, no trunca)."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(RUTA_LOG, "a") as f:
        f.write(f"[{timestamp}] {mensaje}\n")