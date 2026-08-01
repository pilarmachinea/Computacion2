"""
display.py — interfaz visual del monitor (TUI) usando rich.

Arquitectura de este módulo específicamente (distinta al resto del TP):
- El loop PRINCIPAL de display corre en el proceso 'display' (lanzado por
  main.py como multiprocessing.Process, igual que el Recolector y los
  analizadores) y se encarga de leer las vistas compartidas (Manager) y
  pintarlas en pantalla con rich.Live.
- Un THREAD interno (no multiprocessing.Process) lee el teclado en segundo
  plano y empuja las teclas a una queue.Queue local. Se usa thread y no
  proceso acá porque: (a) el trabajo es I/O-bound (esperar una tecla), el
  GIL no es un cuello de botella real; (b) solo hay que compartir eventos
  muy simples (una tecla), no estructuras complejas -- no vale la pena el
  overhead de IPC entre procesos para esto.
"""

import os
import sys
import termios
import tty
import threading
import queue
import time

from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text


VISTAS = ["resumen", "memoria", "fds", "threads", "senales", "scheduling", "sistema"]
TECLAS_VISTA = {"1": "resumen", "2": "memoria", "3": "fds", "4": "threads",
                 "5": "senales", "6": "scheduling", "7": "sistema"}


def leer_teclado_loop(cola_teclas, detener_evento):
    """
    Corre en un thread aparte, DENTRO del proceso de display (un hijo
    creado por multiprocessing). multiprocessing cierra sys.stdin en los
    procesos hijos (lo reemplaza por /dev/null) para evitar que varios
    procesos lean de la misma terminal de forma impredecible -- por eso
    NO se puede usar sys.stdin acá. Se abre /dev/tty directamente, que en
    Linux siempre representa la terminal controladora real del proceso,
    sin pasar por sys.stdin.
    """
    tty_fd = os.open('/dev/tty', os.O_RDONLY)
    config_original = termios.tcgetattr(tty_fd)
    try:
        tty.setcbreak(tty_fd)
        while not detener_evento.is_set():
            tecla = os.read(tty_fd, 1).decode(errors='replace')
            cola_teclas.put(tecla)
    finally:
        termios.tcsetattr(tty_fd, termios.TCSADRAIN, config_original)
        os.close(tty_fd)


def armar_tabla_resumen(filas, pid_pineado=None):
    tabla = Table(title="Resumen de Procesos", expand=True)
    tabla.add_column("PID", justify="right")
    tabla.add_column("Usuario")
    tabla.add_column("Estado")
    tabla.add_column("CPU%", justify="right")
    tabla.add_column("RSS (KB)", justify="right")
    tabla.add_column("Comando")

    for fila in filas[:30]:  # top 30 para no saturar la terminal
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ""
        tabla.add_row(
            str(fila["pid"]), fila["usuario"], fila["state"],
            f"{fila['cpu_pct']:.1f}", str(fila["vmrss_kb"]),
            fila["cmdline"][:50],
            style=estilo,
        )
    return tabla


def armar_tabla_memoria(filas, pid_pineado=None):
    tabla = Table(title="Memoria por Proceso", expand=True)
    tabla.add_column("PID", justify="right")
    tabla.add_column("Comando")
    tabla.add_column("RSS (KB)", justify="right")
    tabla.add_column("Heap (B)", justify="right")
    tabla.add_column("MinFlt", justify="right")
    tabla.add_column("MajFlt", justify="right")

    for fila in filas[:30]:
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ""
        tabla.add_row(
            str(fila["pid"]), fila["comm"], str(fila["vmrss_kb"]),
            str(fila["segmentos"]["heap"]), str(fila["minflt"]), str(fila["majflt"]),
            style=estilo,
        )
    return tabla


def armar_tabla_fds(filas, pid_pineado=None):
    tabla = Table(title="File Descriptors por Proceso", expand=True)
    tabla.add_column("PID", justify="right")
    tabla.add_column("Cantidad FDs", justify="right")

    for fila in filas[:30]:
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ""
        tabla.add_row(str(fila["pid"]), str(fila["cantidad_fds"]), style=estilo)
    return tabla


def armar_tabla_threads(filas, pid_pineado=None):
    tabla = Table(title="Threads (LWPs) por Proceso", expand=True)
    tabla.add_column("PID", justify="right")
    tabla.add_column("Cantidad Threads", justify="right")

    for fila in filas[:30]:
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ""
        tabla.add_row(str(fila["pid"]), str(fila["cantidad_threads"]), style=estilo)
    return tabla


def armar_tabla_senales(filas, pid_pineado=None):
    tabla = Table(title="Señales por Proceso", expand=True)
    tabla.add_column("PID", justify="right")
    tabla.add_column("Ignoradas", overflow="fold")
    tabla.add_column("Con handler", overflow="fold")

    for fila in filas[:30]:
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ""
        tabla.add_row(
            str(fila["pid"]),
            ",".join(fila["ignoradas"][:5]) + ("..." if len(fila["ignoradas"]) > 5 else ""),
            ",".join(fila["con_handler"][:5]) + ("..." if len(fila["con_handler"]) > 5 else ""),
            style=estilo,
        )
    return tabla


def armar_tabla_scheduling(filas, pid_pineado=None):
    tabla = Table(title="Scheduling por Proceso", expand=True)
    tabla.add_column("PID", justify="right")
    tabla.add_column("Comando")
    tabla.add_column("Policy")
    tabla.add_column("Nice", justify="right")
    tabla.add_column("Nonvol CS", justify="right")

    for fila in filas[:30]:
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ""
        tabla.add_row(
            str(fila["pid"]), fila["comm"], fila["policy_nombre"],
            str(fila["nice"]), str(fila["nonvoluntary_ctxt_switches"]),
            style=estilo,
        )
    return tabla


def armar_panel_sistema(datos):
    if not datos:
        return Panel("Esperando datos...", title="Sistema")

    cpu = datos.get("cpu_pct", {})
    load = datos.get("loadavg", {})
    procesos = datos.get("procesos", {})

    texto = Text()
    texto.append(f"CPU:  user={cpu.get('user', 0):.1f}%  "
                 f"system={cpu.get('system', 0):.1f}%  "
                 f"idle={cpu.get('idle', 0):.1f}%  "
                 f"iowait={cpu.get('iowait', 0):.1f}%\n")
    texto.append(f"Load average: {load.get('load_1min', 0):.2f} "
                 f"{load.get('load_5min', 0):.2f} {load.get('load_15min', 0):.2f}\n")
    texto.append(f"Memoria: {datos.get('mem_libre_kb', 0)} / "
                 f"{datos.get('mem_total_kb', 0)} KB libres\n")
    texto.append(f"Procesos: {procesos.get('total_procesos', 0)} totales, "
                 f"{procesos.get('zombies', 0)} zombies, "
                 f"{procesos.get('total_threads', 0)} threads\n")
    texto.append(f"Por estado: {procesos.get('por_estado', {})}")

    return Panel(texto, title="Sistema")


ARMADORES_TABLA = {
    "resumen": armar_tabla_resumen,
    "memoria": armar_tabla_memoria,
    "fds": armar_tabla_fds,
    "threads": armar_tabla_threads,
    "senales": armar_tabla_senales,
    "scheduling": armar_tabla_scheduling,
}


def armar_layout(vista_actual, vistas_compartidas, intervalos, pid_pineado, modo_verbose, ultimo_evento=""):
    """Arma el frame completo que se muestra en cada refresco."""
    layout = Layout()

    if vista_actual == "sistema":
        contenido = armar_panel_sistema(dict(vistas_compartidas["sistema"]))
    else:
        filas = list(vistas_compartidas[vista_actual])
        armador = ARMADORES_TABLA[vista_actual]
        contenido = armador(filas, pid_pineado)

    intervalo_actual = intervalos[vista_actual].value
    linea_atajos = Text(
        f" [1-7] cambiar vista | [+/-] intervalo ({intervalo_actual:.1f}s) | "
        f"[p] pin PID | [q] salir | verbose={'ON' if modo_verbose else 'OFF'} ",
        style="dim",
    )
    partes_pie = [linea_atajos]
    if ultimo_evento:
        partes_pie.append(Text(f" >> {ultimo_evento}", style="bold green"))

    layout.split_column(
        Layout(Panel(f"Monitor de Procesos — Vista: {vista_actual.upper()}", style="bold cyan"), size=3),
        Layout(contenido),
        Layout(Text("\n").join(partes_pie) if len(partes_pie) > 1 else linea_atajos, size=2),
    )
    return layout


def display_loop(vistas_compartidas, intervalos, cola_eventos):
    """
    Proceso principal de la interfaz. Lanzado por main.py como
    multiprocessing.Process, igual que el resto.

    cola_eventos: multiprocessing.Queue por la que main.py comunica
    eventos (dump generado, config recargada, etc.) SIN escribir
    directamente a la terminal -- solo display.py controla la pantalla
    mientras rich.Live está activo.
    """
    from procesos import restaurar_senales_hijo
    restaurar_senales_hijo()

    cola_teclas = queue.Queue()
    detener_evento = threading.Event()
    hilo_teclado = threading.Thread(
        target=leer_teclado_loop, args=(cola_teclas, detener_evento), daemon=True
    )
    hilo_teclado.start()

    vista_actual = "resumen"
    pid_pineado = None
    modo_verbose = False
    ultimo_evento = ""

    try:
        with Live(refresh_per_second=4, screen=True) as live:
            while True:
                while not cola_teclas.empty():
                    tecla = cola_teclas.get_nowait()

                    if tecla == "q":
                        detener_evento.set()
                        return

                    elif tecla in TECLAS_VISTA:
                        vista_actual = TECLAS_VISTA[tecla]

                    elif tecla == "+":
                        with intervalos[vista_actual].get_lock():
                            intervalos[vista_actual].value += 0.5

                    elif tecla == "-":
                        with intervalos[vista_actual].get_lock():
                            intervalos[vista_actual].value = max(
                                0.5, intervalos[vista_actual].value - 0.5
                            )

                    elif tecla == "v":
                        modo_verbose = not modo_verbose

                # Consumir eventos de main.py (sin bloquear). Se queda con
                # el más reciente para mostrarlo en el pie de la pantalla.
                while not cola_eventos.empty():
                    ultimo_evento = cola_eventos.get_nowait()

                live.update(armar_layout(vista_actual, vistas_compartidas, intervalos,
                                          pid_pineado, modo_verbose, ultimo_evento))
                time.sleep(0.1)
    finally:
        detener_evento.set()