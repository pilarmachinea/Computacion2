"""
display.py — interfaz visual del monitor (TUI) usando rich.

Ver notas_teoricas.md (sesiones 11 y 12) para el detalle de por qué el
lector de teclado usa un thread + /dev/tty, y por qué main.py se comunica
con este proceso vía multiprocessing.Queue en vez de print() directo.
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

# Cada vista es accesible por número Y por letra mnemotécnica.
TECLAS_VISTA = {
    "1": "resumen", "r": "resumen",
    "2": "memoria", "m": "memoria",
    "3": "fds", "f": "fds",
    "4": "threads", "t": "threads",
    "5": "senales", "s": "senales",
    "6": "scheduling", "p": "scheduling",
    "7": "sistema", "g": "sistema",
}

INTERVALOS_MINIMOS = {
    "recolector": 0.5, "resumen": 0.5, "memoria": 1, "fds": 2,
    "threads": 0.5, "senales": 5, "scheduling": 5, "sistema": 1,
}

TEXTO_AYUDA = """
[bold]Atajos de teclado[/bold]

  1-7  o  r/m/f/t/s/p/g     cambiar de vista
  ↑ / ↓                     mover el cursor de selección
  Enter                     pinear/despinear el proceso seleccionado
  /                         filtrar por nombre de comando (Enter para aplicar, Esc cancela)
  u                         filtrar por usuario (Enter para aplicar, Esc cancela)
  c                         cambiar criterio de orden (CPU% / RSS / PID) -- vista Resumen
  + / -                     ajustar el intervalo de refresco de la vista actual
  h  o  ?                   esta ayuda
  q                         salir

Presioná cualquier tecla para cerrar esta ayuda.
"""


def leer_teclado_loop(cola_teclas, detener_evento):
    """
    Corre en un thread aparte, DENTRO del proceso de display (un hijo de
    multiprocessing). Usa /dev/tty en vez de sys.stdin porque
    multiprocessing cierra sys.stdin en los procesos hijos (lo reemplaza
    por /dev/null) -- ver notas_teoricas.md sesión 11.
    Reconoce además las secuencias de escape de las flechas (ESC [ A/B).
    """
    tty_fd = os.open('/dev/tty', os.O_RDONLY)
    config_original = termios.tcgetattr(tty_fd)
    try:
        tty.setcbreak(tty_fd)
        while not detener_evento.is_set():
            caracter = os.read(tty_fd, 1).decode(errors='replace')

            if caracter == '\x1b':  # posible secuencia de flecha: ESC [ A/B
                resto = os.read(tty_fd, 2).decode(errors='replace')
                if resto == '[A':
                    cola_teclas.put("ARRIBA")
                elif resto == '[B':
                    cola_teclas.put("ABAJO")
                else:
                    cola_teclas.put("ESC")
            else:
                cola_teclas.put(caracter)
    finally:
        termios.tcsetattr(tty_fd, termios.TCSADRAIN, config_original)
        os.close(tty_fd)


# ---------------------------------------------------------------------------
# Armado de tablas por vista
# ---------------------------------------------------------------------------

def _filtrar_filas(filas, filtro_usuario, filtro_comando, campo_usuario="usuario", campo_comando="cmdline"):
    """Aplica los filtros activos (si hay) sobre la lista de filas."""
    resultado = filas
    if filtro_usuario:
        resultado = [f for f in resultado if filtro_usuario.lower() in str(f.get(campo_usuario, "")).lower()]
    if filtro_comando:
        resultado = [f for f in resultado if filtro_comando.lower() in str(f.get(campo_comando, f.get("comm", ""))).lower()]
    return resultado


def armar_tabla_resumen(filas, pid_pineado, indice_cursor, orden):
    campos_orden = {"cpu": ("cpu_pct", True), "rss": ("vmrss_kb", True), "pid": ("pid", False)}
    campo, descendente = campos_orden.get(orden, campos_orden["cpu"])
    filas = sorted(filas, key=lambda f: f[campo], reverse=descendente)

    tabla = Table(title=f"Resumen de Procesos (orden: {orden})", expand=True)
    tabla.add_column("", width=2)
    tabla.add_column("PID", justify="right")
    tabla.add_column("Usuario")
    tabla.add_column("Estado")
    tabla.add_column("CPU%", justify="right")
    tabla.add_column("RSS (KB)", justify="right")
    tabla.add_column("Comando")

    for i, fila in enumerate(filas[:30]):
        marca = "📌" if fila["pid"] == pid_pineado else (">" if i == indice_cursor else "")
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ("reverse" if i == indice_cursor else "")
        tabla.add_row(
            marca, str(fila["pid"]), fila["usuario"], fila["state"],
            f"{fila['cpu_pct']:.1f}", str(fila["vmrss_kb"]), fila["cmdline"][:50],
            style=estilo,
        )
    return tabla, filas


def _tabla_generica(filas, pid_pineado, indice_cursor, titulo, columnas, extractor):
    tabla = Table(title=titulo, expand=True)
    tabla.add_column("", width=2)
    for nombre_col in columnas:
        tabla.add_column(nombre_col, justify="right" if nombre_col != "Comando" else "left")

    for i, fila in enumerate(filas[:30]):
        marca = "📌" if fila["pid"] == pid_pineado else (">" if i == indice_cursor else "")
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ("reverse" if i == indice_cursor else "")
        tabla.add_row(marca, *extractor(fila), style=estilo)
    return tabla


def armar_tabla_memoria(filas, pid_pineado, indice_cursor, orden):
    return _tabla_generica(
        filas, pid_pineado, indice_cursor, "Memoria por Proceso",
        ["PID", "Comando", "RSS (KB)", "Heap (B)", "MinFlt", "MajFlt"],
        lambda f: (str(f["pid"]), f["comm"], str(f["vmrss_kb"]),
                   str(f["segmentos"]["heap"]), str(f["minflt"]), str(f["majflt"])),
    )


def armar_tabla_fds(filas, pid_pineado, indice_cursor, orden):
    return _tabla_generica(
        filas, pid_pineado, indice_cursor, "File Descriptors por Proceso",
        ["PID", "Cantidad FDs"],
        lambda f: (str(f["pid"]), str(f["cantidad_fds"])),
    )


def armar_tabla_threads(filas, pid_pineado, indice_cursor, orden):
    return _tabla_generica(
        filas, pid_pineado, indice_cursor, "Threads (LWPs) por Proceso",
        ["PID", "Cantidad Threads"],
        lambda f: (str(f["pid"]), str(f["cantidad_threads"])),
    )


def armar_tabla_senales(filas, pid_pineado, indice_cursor, orden):
    return _tabla_generica(
        filas, pid_pineado, indice_cursor, "Señales por Proceso",
        ["PID", "Ignoradas", "Con handler"],
        lambda f: (str(f["pid"]),
                   ",".join(f["ignoradas"][:5]) + ("..." if len(f["ignoradas"]) > 5 else ""),
                   ",".join(f["con_handler"][:5]) + ("..." if len(f["con_handler"]) > 5 else "")),
    )


def armar_tabla_scheduling(filas, pid_pineado, indice_cursor, orden):
    return _tabla_generica(
        filas, pid_pineado, indice_cursor, "Scheduling por Proceso",
        ["PID", "Comando", "Policy", "Nice", "Nonvol CS"],
        lambda f: (str(f["pid"]), f["comm"], f["policy_nombre"],
                   str(f["nice"]), str(f["nonvoluntary_ctxt_switches"])),
    )


def armar_panel_sistema(datos):
    if not datos:
        return Panel("Esperando datos...", title="Sistema")

    cpu = datos.get("cpu_pct", {})
    load = datos.get("loadavg", {})
    procesos = datos.get("procesos", {})

    texto = Text()
    texto.append(f"CPU:  user={cpu.get('user', 0):.1f}%  system={cpu.get('system', 0):.1f}%  "
                 f"idle={cpu.get('idle', 0):.1f}%  iowait={cpu.get('iowait', 0):.1f}%\n")
    texto.append(f"Load average: {load.get('load_1min', 0):.2f} "
                 f"{load.get('load_5min', 0):.2f} {load.get('load_15min', 0):.2f}\n")
    texto.append(f"Memoria: {datos.get('mem_libre_kb', 0)} / {datos.get('mem_total_kb', 0)} KB libres\n")
    texto.append(f"Procesos: {procesos.get('total_procesos', 0)} totales, "
                 f"{procesos.get('zombies', 0)} zombies, {procesos.get('total_threads', 0)} threads\n")
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


def armar_layout(estado):
    """
    estado: dict con todo el estado mutable del display (vista actual,
    cursor, filtros, pin, último evento, etc.) -- agrupado así para no
    pasar 10 parámetros sueltos entre funciones.
    """
    layout = Layout()
    vista_actual = estado["vista_actual"]

    if vista_actual == "sistema":
        contenido = armar_panel_sistema(dict(estado["vistas_compartidas"]["sistema"]))
        filas_visibles = []
    else:
        filas_crudas = list(estado["vistas_compartidas"][vista_actual])
        filas_filtradas = _filtrar_filas(filas_crudas, estado["filtro_usuario"], estado["filtro_comando"])

        armador = ARMADORES_TABLA[vista_actual]
        if vista_actual == "resumen":
            contenido, filas_visibles = armador(filas_filtradas, estado["pid_pineado"],
                                                 estado["indice_cursor"], estado["orden"])
        else:
            filas_visibles = filas_filtradas[:30]
            contenido = armador(filas_filtradas, estado["pid_pineado"],
                                 estado["indice_cursor"], estado["orden"])

    intervalo_actual = estado["intervalos"][vista_actual].value
    texto_pin = f"PID {estado['pid_pineado']} 📌" if estado["pid_pineado"] else "sin pin"
    filtros_activos = []
    if estado["filtro_usuario"]:
        filtros_activos.append(f"usuario~{estado['filtro_usuario']}")
    if estado["filtro_comando"]:
        filtros_activos.append(f"comando~{estado['filtro_comando']}")
    texto_filtros = " | ".join(filtros_activos) if filtros_activos else "sin filtros"

    linea_atajos = Text(
        f" [1-7/letras] vista | [↑↓] cursor | [Enter] pin ({texto_pin}) | [/] filtro cmd | "
        f"[u] filtro user ({texto_filtros}) | [c] orden | [+/-] intervalo ({intervalo_actual:.1f}s) | "
        f"[h] ayuda | [q] salir ",
        style="dim",
    )

    partes_pie = [linea_atajos]
    if estado["modo_entrada"]:
        prompt = "Filtrar por comando" if estado["modo_entrada"] == "comando" else "Filtrar por usuario"
        partes_pie.append(Text(f" {prompt}: {estado['buffer_entrada']}_", style="bold cyan"))
    elif estado["ultimo_evento"]:
        partes_pie.append(Text(f" >> {estado['ultimo_evento']}", style="bold green"))

    if estado["mostrar_ayuda"]:
        cuerpo = Panel(Text.from_markup(TEXTO_AYUDA), title="Ayuda")
    else:
        cuerpo = contenido

    layout.split_column(
        Layout(Panel(f"Monitor de Procesos — Vista: {vista_actual.upper()}", style="bold cyan"), size=3),
        Layout(cuerpo),
        Layout(Text("\n").join(partes_pie) if len(partes_pie) > 1 else linea_atajos, size=2),
    )
    return layout, filas_visibles


def display_loop(vistas_compartidas, intervalos, cola_eventos):
    """Proceso principal de la interfaz, lanzado por main.py."""
    from procesos import restaurar_senales_hijo
    restaurar_senales_hijo()

    cola_teclas = queue.Queue()
    detener_evento = threading.Event()
    hilo_teclado = threading.Thread(
        target=leer_teclado_loop, args=(cola_teclas, detener_evento), daemon=True
    )
    hilo_teclado.start()

    estado = {
        "vista_actual": "resumen",
        "vistas_compartidas": vistas_compartidas,
        "intervalos": intervalos,
        "pid_pineado": None,
        "indice_cursor": 0,
        "orden": "cpu",  # cpu | rss | pid -- toggled con 'c', solo aplica a Resumen
        "filtro_usuario": None,
        "filtro_comando": None,
        "modo_entrada": None,   # None | "usuario" | "comando" -- mientras se escribe un filtro
        "buffer_entrada": "",
        "ultimo_evento": "",
        "mostrar_ayuda": False,
        "modo_verbose": False,
    }

    try:
        with Live(refresh_per_second=4, screen=True) as live:
            while True:
                layout, filas_visibles = armar_layout(estado)

                while not cola_teclas.empty():
                    tecla = cola_teclas.get_nowait()
                    _procesar_tecla(tecla, estado, filas_visibles, detener_evento)
                    if estado.get("_salir"):
                        return

                while not cola_eventos.empty():
                    estado["ultimo_evento"] = cola_eventos.get_nowait()

                live.update(layout)
                time.sleep(0.1)
    finally:
        detener_evento.set()


def _procesar_tecla(tecla, estado, filas_visibles, detener_evento):
    """Aplica el efecto de una tecla sobre el estado mutable del display."""

    # Modo ayuda: cualquier tecla la cierra, sin procesar nada más.
    if estado["mostrar_ayuda"]:
        estado["mostrar_ayuda"] = False
        return

    # Modo entrada de texto (filtro por comando o usuario).
    if estado["modo_entrada"]:
        if tecla in ("\r", "\n"):
            if estado["modo_entrada"] == "comando":
                estado["filtro_comando"] = estado["buffer_entrada"] or None
            else:
                estado["filtro_usuario"] = estado["buffer_entrada"] or None
            estado["modo_entrada"] = None
            estado["buffer_entrada"] = ""
        elif tecla == "ESC":
            estado["modo_entrada"] = None
            estado["buffer_entrada"] = ""
        elif tecla in ("\x7f", "\b"):  # backspace
            estado["buffer_entrada"] = estado["buffer_entrada"][:-1]
        elif tecla.isprintable():
            estado["buffer_entrada"] += tecla
        return

    if tecla == "q":
        detener_evento.set()
        estado["_salir"] = True
        return

    if tecla in ("h", "?"):
        estado["mostrar_ayuda"] = True
        return

    if tecla in TECLAS_VISTA:
        estado["vista_actual"] = TECLAS_VISTA[tecla]
        estado["indice_cursor"] = 0
        return

    if tecla == "ARRIBA":
        estado["indice_cursor"] = max(0, estado["indice_cursor"] - 1)
        return

    if tecla == "ABAJO":
        tope = max(0, len(filas_visibles) - 1)
        estado["indice_cursor"] = min(tope, estado["indice_cursor"] + 1)
        return

    if tecla in ("\r", "\n"):  # Enter: pin/unpin del proceso bajo el cursor
        if estado["vista_actual"] != "sistema" and filas_visibles:
            idx = min(estado["indice_cursor"], len(filas_visibles) - 1)
            pid_seleccionado = filas_visibles[idx]["pid"]
            estado["pid_pineado"] = None if estado["pid_pineado"] == pid_seleccionado else pid_seleccionado
        return

    if tecla == "/":
        estado["modo_entrada"] = "comando"
        estado["buffer_entrada"] = ""
        return

    if tecla == "u":
        estado["modo_entrada"] = "usuario"
        estado["buffer_entrada"] = ""
        return

    if tecla == "c":
        orden_actual = estado["orden"]
        estado["orden"] = {"cpu": "rss", "rss": "pid", "pid": "cpu"}[orden_actual]
        return

    if tecla == "+":
        v = estado["intervalos"][estado["vista_actual"]]
        with v.get_lock():
            v.value += 0.5
        return

    if tecla == "-":
        vista_actual = estado["vista_actual"]
        v = estado["intervalos"][vista_actual]
        minimo = INTERVALOS_MINIMOS.get(vista_actual, 0.5)
        with v.get_lock():
            v.value = max(minimo, v.value - 0.5)
        return