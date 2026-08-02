"""
display.py — interfaz visual del monitor (TUI) usando rich.

Ver notas_teoricas.md (sesiones 11, 12, 14) para el detalle de por qué el
lector de teclado usa un thread + /dev/tty, por qué main.py se comunica
con este proceso vía multiprocessing.Queue en vez de print() directo, y
por qué hay que levantar el contenedor con 'docker compose run', no 'up'.
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
from rich.console import Group


VISTAS = ["resumen", "memoria", "fds", "threads", "senales", "scheduling", "sistema"]

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

# Vistas donde cada fila es un proceso con una LISTA anidada de ítems
# (FDs, threads) que no entra como columna -- se muestran en un panel
# de detalle aparte, para el proceso bajo el cursor.
VISTAS_CON_DETALLE = {
    "fds": ("fds", ["FD", "Destino", "Tipo"], lambda item: (str(item["fd"]), item["destino"][:40], item["tipo"])),
    "threads": ("threads", ["TID", "Comm", "Estado", "UTime", "STime", "Vol CS", "NoVol CS"],
                lambda item: (str(item["tid"]), item["comm"], item["state"],
                               str(item["utime"]), str(item["stime"]),
                               str(item["voluntary_ctxt_switches"]), str(item["nonvoluntary_ctxt_switches"]))),
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

En las vistas FDs y Threads, el panel inferior muestra el detalle
completo del proceso bajo el cursor (lista de FDs o de threads).

Presioná cualquier tecla para cerrar esta ayuda.
"""


def leer_teclado_loop(cola_teclas, detener_evento):
    """
    Corre en un thread aparte, DENTRO del proceso de display (un hijo de
    multiprocessing). Usa /dev/tty en vez de sys.stdin porque
    multiprocessing cierra sys.stdin en los procesos hijos -- ver
    notas_teoricas.md sesión 11.
    """
    tty_fd = os.open('/dev/tty', os.O_RDONLY)
    config_original = termios.tcgetattr(tty_fd)
    try:
        tty.setcbreak(tty_fd)
        while not detener_evento.is_set():
            caracter = os.read(tty_fd, 1).decode(errors='replace')
            if caracter == '\x1b':
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
    tabla.add_column("PPID", justify="right")
    tabla.add_column("Usuario")
    tabla.add_column("Estado")
    tabla.add_column("Threads", justify="right")
    tabla.add_column("CPU%", justify="right")
    tabla.add_column("RSS (KB)", justify="right")
    tabla.add_column("Comando")

    for i, fila in enumerate(filas[:30]):
        marca = "📌" if fila["pid"] == pid_pineado else (">" if i == indice_cursor else "")
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ("reverse" if i == indice_cursor else "")
        tabla.add_row(
            marca, str(fila["pid"]), str(fila["ppid"]), fila["usuario"], fila["state"],
            str(fila["threads"]), f"{fila['cpu_pct']:.1f}", str(fila["vmrss_kb"]), fila["cmdline"][:40],
            style=estilo,
        )
    return tabla, filas


def armar_tabla_memoria(filas, pid_pineado, indice_cursor, orden):
    tabla = Table(title="Memoria por Proceso", expand=True)
    tabla.add_column("", width=2)
    tabla.add_column("PID", justify="right")
    tabla.add_column("Comando")
    tabla.add_column("VmSize", justify="right")
    tabla.add_column("VmRSS", justify="right")
    tabla.add_column("VmData", justify="right")
    tabla.add_column("VmStk", justify="right")
    tabla.add_column("VmExe", justify="right")
    tabla.add_column("VmLib", justify="right")
    tabla.add_column("VmHWM", justify="right")
    tabla.add_column("VmSwap", justify="right")
    tabla.add_column("MinFlt", justify="right")
    tabla.add_column("MajFlt", justify="right")
    tabla.add_column("Segmentos (text/data/heap/stack/shared, KB)")

    for i, fila in enumerate(filas[:30]):
        marca = "📌" if fila["pid"] == pid_pineado else (">" if i == indice_cursor else "")
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ("reverse" if i == indice_cursor else "")
        seg = fila["segmentos"]
        texto_segmentos = (f"{seg['text']//1024}/{seg['data']//1024}/"
                            f"{seg['heap']//1024}/{seg['stack']//1024}/{seg['shared']//1024}")
        tabla.add_row(
            marca, str(fila["pid"]), fila["comm"][:20],
            str(fila["vmsize_kb"]), str(fila["vmrss_kb"]), str(fila["vmdata_kb"]),
            str(fila["vmstk_kb"]), str(fila["vmexe_kb"]), str(fila["vmlib_kb"]),
            str(fila["vmhwm_kb"]), str(fila["vmswap_kb"]),
            str(fila["minflt"]), str(fila["majflt"]), texto_segmentos,
            style=estilo,
        )
    return tabla, filas


def _tabla_con_conteo(filas, pid_pineado, indice_cursor, titulo, nombre_campo_cantidad, etiqueta_cantidad):
    tabla = Table(title=titulo, expand=True)
    tabla.add_column("", width=2)
    tabla.add_column("PID", justify="right")
    tabla.add_column("Comando")
    tabla.add_column(etiqueta_cantidad, justify="right")

    for i, fila in enumerate(filas[:30]):
        marca = "📌" if fila["pid"] == pid_pineado else (">" if i == indice_cursor else "")
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ("reverse" if i == indice_cursor else "")
        tabla.add_row(marca, str(fila["pid"]), fila.get("comm", "")[:30] if fila.get("comm") else "",
                      str(fila[nombre_campo_cantidad]), style=estilo)
    return tabla, filas


def armar_tabla_fds(filas, pid_pineado, indice_cursor, orden):
    return _tabla_con_conteo(filas, pid_pineado, indice_cursor,
                              "File Descriptors por Proceso (Enter/cursor para ver detalle abajo)",
                              "cantidad_fds", "Cantidad FDs")


def armar_tabla_threads(filas, pid_pineado, indice_cursor, orden):
    return _tabla_con_conteo(filas, pid_pineado, indice_cursor,
                              "Threads (LWPs) por Proceso (Enter/cursor para ver detalle abajo)",
                              "cantidad_threads", "Cantidad Threads")


def armar_tabla_senales(filas, pid_pineado, indice_cursor, orden):
    tabla = Table(title="Señales por Proceso", expand=True)
    tabla.add_column("", width=2)
    tabla.add_column("PID", justify="right")
    tabla.add_column("Bloqueadas", overflow="fold")
    tabla.add_column("Ignoradas", overflow="fold")
    tabla.add_column("Con handler", overflow="fold")
    tabla.add_column("Pend. proceso", overflow="fold")
    tabla.add_column("Pend. grupo", overflow="fold")

    def _resumir(lista):
        if not lista:
            return "-"
        texto = ",".join(lista[:3])
        return texto + (f" (+{len(lista)-3})" if len(lista) > 3 else "")

    for i, fila in enumerate(filas[:30]):
        marca = "📌" if fila["pid"] == pid_pineado else (">" if i == indice_cursor else "")
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ("reverse" if i == indice_cursor else "")
        tabla.add_row(
            marca, str(fila["pid"]),
            _resumir(fila["bloqueadas"]), _resumir(fila["ignoradas"]),
            _resumir(fila["con_handler"]), _resumir(fila["pendientes_proceso"]),
            _resumir(fila["pendientes_grupo"]),
            style=estilo,
        )
    return tabla, filas


def armar_tabla_scheduling(filas, pid_pineado, indice_cursor, orden):
    tabla = Table(title="Scheduling por Proceso", expand=True)
    tabla.add_column("", width=2)
    tabla.add_column("PID", justify="right")
    tabla.add_column("SID", justify="right")
    tabla.add_column("PGID", justify="right")
    tabla.add_column("Comando")
    tabla.add_column("Policy")
    tabla.add_column("Priority", justify="right")
    tabla.add_column("Nice", justify="right")
    tabla.add_column("RT Prio", justify="right")
    tabla.add_column("CPU Affinity")
    tabla.add_column("Vol CS", justify="right")
    tabla.add_column("Nonvol CS", justify="right")

    for i, fila in enumerate(filas[:30]):
        marca = "📌" if fila["pid"] == pid_pineado else (">" if i == indice_cursor else "")
        estilo = "bold yellow" if fila["pid"] == pid_pineado else ("reverse" if i == indice_cursor else "")
        tabla.add_row(
            marca, str(fila["pid"]), str(fila["sid"]), str(fila["pgid"]), fila["comm"][:20],
            fila["policy_nombre"], str(fila["priority"]), str(fila["nice"]), str(fila["rt_priority"]),
            fila["cpus_allowed_list"], str(fila["voluntary_ctxt_switches"]),
            str(fila["nonvoluntary_ctxt_switches"]),
            style=estilo,
        )
    return tabla, filas


def armar_panel_sistema(datos):
    if not datos:
        return Panel("Esperando datos...", title="Sistema")

    cpu = datos.get("cpu_pct", {})
    load = datos.get("loadavg", {})
    procesos = datos.get("procesos", {})

    texto = Text()
    texto.append(f"CPU:  user={cpu.get('user', 0):.1f}%  system={cpu.get('system', 0):.1f}%  "
                 f"idle={cpu.get('idle', 0):.1f}%  iowait={cpu.get('iowait', 0):.1f}%\n")
    texto.append(f"Load average (1/5/15 min): {load.get('load_1min', 0):.2f} "
                 f"{load.get('load_5min', 0):.2f} {load.get('load_15min', 0):.2f}\n")
    texto.append(f"Uptime: {datos.get('uptime_seg', 0):.0f} segundos\n")
    texto.append(f"Memoria: {datos.get('mem_libre_kb', 0)} libres / {datos.get('mem_total_kb', 0)} KB totales "
                 f"(buffers {datos.get('mem_buffers_kb', 0)} KB, cache {datos.get('mem_cached_kb', 0)} KB)\n")
    texto.append(f"Swap: {datos.get('swap_libre_kb', 0)} libres / {datos.get('swap_total_kb', 0)} KB totales\n")
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


def _armar_panel_detalle(vista_actual, filas_visibles, indice_cursor):
    """
    Para FDs y Threads: arma la tabla de detalle (lista de FDs o de threads)
    del proceso bajo el cursor, ya que esos datos no entran como columna
    en la tabla principal (son una lista por proceso, no un escalar).
    """
    if vista_actual not in VISTAS_CON_DETALLE or not filas_visibles:
        return None

    campo_lista, columnas, extractor = VISTAS_CON_DETALLE[vista_actual]
    idx = min(indice_cursor, len(filas_visibles) - 1)
    fila_seleccionada = filas_visibles[idx]
    items = fila_seleccionada.get(campo_lista, [])

    tabla = Table(title=f"Detalle de PID {fila_seleccionada['pid']} ({len(items)} ítems)", expand=True)
    for col in columnas:
        tabla.add_column(col)
    for item in items[:20]:
        tabla.add_row(*extractor(item))
    if len(items) > 20:
        tabla.caption = f"... y {len(items) - 20} más"
    return tabla


def armar_layout(estado):
    layout = Layout()
    vista_actual = estado["vista_actual"]

    if vista_actual == "sistema":
        contenido = armar_panel_sistema(dict(estado["vistas_compartidas"]["sistema"]))
        filas_visibles = []
    else:
        filas_crudas = list(estado["vistas_compartidas"][vista_actual])
        filas_filtradas = _filtrar_filas(filas_crudas, estado["filtro_usuario"], estado["filtro_comando"])

        armador = ARMADORES_TABLA[vista_actual]
        tabla_principal, filas_visibles = armador(filas_filtradas, estado["pid_pineado"],
                                                    estado["indice_cursor"], estado["orden"])

        panel_detalle = _armar_panel_detalle(vista_actual, filas_visibles, estado["indice_cursor"])
        contenido = Group(tabla_principal, panel_detalle) if panel_detalle else tabla_principal

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
        "orden": "cpu",
        "filtro_usuario": None,
        "filtro_comando": None,
        "modo_entrada": None,
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
    if estado["mostrar_ayuda"]:
        estado["mostrar_ayuda"] = False
        return

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
        elif tecla in ("\x7f", "\b"):
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

    if tecla in ("\r", "\n"):
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