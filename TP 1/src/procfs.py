"""
procfs.py — helpers para leer y parsear archivos de /proc.

Responsabilidad de este módulo: "saber hablar con el sistema" (Linux /proc).
No sabe nada de multiprocessing, de intervalos de recolección, ni de cómo
se muestra la información — eso es responsabilidad de recolector.py,
cpu.py y los analizadores/.
"""

import os
import pwd


# ---------------------------------------------------------------------------
# stat: /proc/<pid>/stat y /proc/<pid>/task/<tid>/stat (mismo formato)
# ---------------------------------------------------------------------------

def _leer_stat_desde_ruta(ruta):
    """
    Parsea un archivo con el formato de /proc/<pid>/stat.
    No agrega pid/tid: eso lo hacen las funciones públicas que sí
    conocen esos valores.
    """
    try:
        with open(ruta, "r") as f:
            linea = f.read().strip()
        # El campo 'comm' (nombre del proceso/thread) va entre paréntesis
        # y puede contener espacios, por eso se usa rfind(')') en vez de
        # un split(' ') ingenuo sobre toda la línea.
        fin_comm = linea.rfind(')')
        comm = linea[linea.find('(') + 1:fin_comm]
        resto = linea[fin_comm + 2:].split()
        # índice_en_resto = campo_del_enunciado - 3
        return {
            "comm": comm,
            "state": resto[0],            # campo 3
            "ppid": int(resto[1]),        # campo 4
            "minflt": int(resto[7]),      # campo 10
            "cminflt": int(resto[8]),     # campo 11
            "majflt": int(resto[9]),      # campo 12
            "cmajflt": int(resto[10]),    # campo 13
            "utime": int(resto[11]),      # campo 14
            "stime": int(resto[12]),      # campo 15
        }
    except (FileNotFoundError, PermissionError):
        return None


def leer_stat(pid):
    """Lee /proc/<pid>/stat. Devuelve None si el proceso no existe o no hay permiso."""
    datos = _leer_stat_desde_ruta(f"/proc/{pid}/stat")
    if datos is not None:
        datos["pid"] = pid
    return datos


def leer_stat_thread(pid, tid):
    """Lee /proc/<pid>/task/<tid>/stat (mismo formato que stat de proceso)."""
    datos = _leer_stat_desde_ruta(f"/proc/{pid}/task/{tid}/stat")
    if datos is not None:
        datos["pid"] = pid
        datos["tid"] = tid
    return datos


# ---------------------------------------------------------------------------
# status: /proc/<pid>/status y /proc/<pid>/task/<tid>/status (mismo formato)
# ---------------------------------------------------------------------------

def _leer_status_desde_ruta(ruta):
    """
    Parsea un archivo con el formato de /proc/<pid>/status:
    líneas 'Clave:\tValor', sin trampas de espacios como en stat.
    """
    try:
        datos = {}
        with open(ruta, "r") as f:
            for linea in f:
                clave, _, valor = linea.partition(':')
                datos[clave.strip()] = valor.strip()
        return datos
    except (FileNotFoundError, PermissionError):
        return None


def leer_status(pid):
    """Lee /proc/<pid>/status. Devuelve un dict con TODAS las claves como string."""
    return _leer_status_desde_ruta(f"/proc/{pid}/status")


def leer_status_thread(pid, tid):
    """Lee /proc/<pid>/task/<tid>/status (mismo formato que status de proceso)."""
    return _leer_status_desde_ruta(f"/proc/{pid}/task/{tid}/status")


# ---------------------------------------------------------------------------
# Helpers de conversión / traducción
# ---------------------------------------------------------------------------

def _kb_a_int(texto):
    """'9984 kB' -> 9984"""
    return int(texto.split()[0])


def _bytes_a_legible(cantidad_bytes):
    """1536000 -> '1.5 MB'"""
    for unidad in ['B', 'KB', 'MB', 'GB']:
        if cantidad_bytes < 1024:
            return f"{cantidad_bytes:.1f} {unidad}"
        cantidad_bytes /= 1024
    return f"{cantidad_bytes:.1f} TB"


def usuario_de(uid):
    """Traduce un UID numérico a nombre de usuario. Si no existe, devuelve el UID como string."""
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


# ---------------------------------------------------------------------------
# Listado de procesos
# ---------------------------------------------------------------------------

def listar_pids():
    """Devuelve la lista de PIDs activos en el sistema."""
    pids = []
    for entrada in os.listdir("/proc"):
        if entrada.isdigit():
            pids.append(int(entrada))
    return pids


# ---------------------------------------------------------------------------
# cmdline: /proc/<pid>/cmdline
# ---------------------------------------------------------------------------

def leer_cmdline(pid):
    """
    Lee /proc/<pid>/cmdline. A diferencia de stat/status, los argumentos
    están separados por bytes nulos (\\x00), no por espacios.
    """
    ruta = f"/proc/{pid}/cmdline"
    try:
        with open(ruta, "rb") as f:
            data = f.read()
        if not data:
            return ""
        partes = data.split(b'\x00')
        return ' '.join(p.decode(errors='replace') for p in partes if p)
    except (FileNotFoundError, PermissionError):
        return None


# ---------------------------------------------------------------------------
# maps: /proc/<pid>/maps — segmentos de memoria virtual agrupados
# ---------------------------------------------------------------------------

def _clasificar_segmento(path, permisos):
    if path == "[heap]":
        return "heap"
    if path == "[stack]":
        return "stack"
    if path and 'x' in permisos:
        return "text"
    if path:
        return "data"
    return "shared"


def leer_maps(pid):
    """
    Lee /proc/<pid>/maps y agrupa los segmentos por categoría,
    sumando el tamaño total (en bytes) de cada una.
    Categorías: text, data, heap, stack, shared.
    """
    ruta = f"/proc/{pid}/maps"
    totales = {"text": 0, "data": 0, "heap": 0, "stack": 0, "shared": 0}

    try:
        with open(ruta, "r") as f:
            for linea in f:
                partes = linea.split()
                rango = partes[0]
                permisos = partes[1]
                path = partes[5] if len(partes) > 5 else ""

                inicio_hex, fin_hex = rango.split('-')
                tamano = int(fin_hex, 16) - int(inicio_hex, 16)

                categoria = _clasificar_segmento(path, permisos)
                totales[categoria] += tamano

        return totales
    except (FileNotFoundError, PermissionError):
        return None


# ---------------------------------------------------------------------------
# fd: /proc/<pid>/fd — file descriptors abiertos
# ---------------------------------------------------------------------------

def _clasificar_fd(destino):
    if destino.startswith("socket:"):
        return "socket"
    if destino.startswith("pipe:"):
        return "pipe"
    if destino.startswith("/dev/pts/") or destino.startswith("/dev/tty"):
        return "tty"
    if destino.startswith("/dev/"):
        return "dispositivo"
    if destino.startswith("/"):
        return "file"
    return "otro"


def leer_fds(pid):
    """
    Lista los FDs abiertos por un proceso y clasifica cada uno
    según su destino (tty, socket, pipe, file, dispositivo, otro).
    """
    ruta = f"/proc/{pid}/fd"
    fds = []

    try:
        entradas = os.listdir(ruta)
    except (FileNotFoundError, PermissionError):
        return None

    for fd_num in entradas:
        try:
            destino = os.readlink(f"{ruta}/{fd_num}")
        except (FileNotFoundError, PermissionError):
            # el FD individual pudo cerrarse entre el listado y esta lectura
            continue

        fds.append({
            "fd": int(fd_num),
            "destino": destino,
            "tipo": _clasificar_fd(destino),
        })

    return fds


# ---------------------------------------------------------------------------
# Combinaciones de alto nivel para cada vista
# ---------------------------------------------------------------------------

def resumen_de(pid):
    """
    Combina leer_stat + leer_status + leer_cmdline y devuelve los campos
    tipados que necesita la vista Resumen.
    """
    stat = leer_stat(pid)
    status = leer_status(pid)
    if stat is None or status is None:
        return None

    uid = int(status["Uid"].split()[0])
    gid = int(status["Gid"].split()[0])
    cmdline = leer_cmdline(pid)

    return {
        "pid": pid,
        "comm": stat["comm"],
        "ppid": stat["ppid"],
        "state": status["State"].split()[0],
        "threads": int(status["Threads"]),
        "vmrss_kb": _kb_a_int(status.get("VmRSS", "0 kB")),
        "uid": uid,
        "gid": gid,
        "usuario": usuario_de(uid),
        "cmdline": cmdline if cmdline else f"[{stat['comm']}]",
        "utime": stat["utime"],
        "stime": stat["stime"],
    }


def memoria_de(pid):
    """
    Combina leer_stat + leer_status + leer_maps y devuelve
    los campos tipados que necesita la vista Memoria.
    """
    stat = leer_stat(pid)
    status = leer_status(pid)
    segmentos = leer_maps(pid)
    if stat is None or status is None or segmentos is None:
        return None

    return {
        "pid": pid,
        "comm": stat["comm"],
        "vmsize_kb": _kb_a_int(status.get("VmSize", "0 kB")),
        "vmrss_kb": _kb_a_int(status.get("VmRSS", "0 kB")),
        "vmdata_kb": _kb_a_int(status.get("VmData", "0 kB")),
        "vmstk_kb": _kb_a_int(status.get("VmStk", "0 kB")),
        "vmexe_kb": _kb_a_int(status.get("VmExe", "0 kB")),
        "vmlib_kb": _kb_a_int(status.get("VmLib", "0 kB")),
        "vmhwm_kb": _kb_a_int(status.get("VmHWM", "0 kB")),
        "vmswap_kb": _kb_a_int(status.get("VmSwap", "0 kB")),
        "minflt": stat["minflt"],
        "majflt": stat["majflt"],
        "segmentos": segmentos,  # dict: {text, data, heap, stack, shared} en bytes
    }


def leer_threads(pid):
    """
    Devuelve una lista con información de cada thread (LWP):
    tid, comm, state, utime, stime y cambios de contexto.
    """
    ruta_task = f"/proc/{pid}/task"
    threads = []

    try:
        entradas = os.listdir(ruta_task)
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None

    for entrada in entradas:
        if not entrada.isdigit():
            continue

        tid = int(entrada)

        stat = leer_stat_thread(pid, tid)
        status = leer_status_thread(pid, tid)

        if stat is None or status is None:
            continue

        ruta_comm = f"/proc/{pid}/task/{tid}/comm"

        try:
            with open(ruta_comm, "r") as archivo:
                comm = archivo.read().strip()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue

        try:
            threads.append({
                "pid": pid,
                "tid": tid,
                "comm": comm,
                "state": status.get("State", stat["state"]).split()[0],
                "utime": stat["utime"],
                "stime": stat["stime"],
                "voluntary_ctxt_switches": int(
                    status.get("voluntary_ctxt_switches", "0")
                ),
                "nonvoluntary_ctxt_switches": int(
                    status.get("nonvoluntary_ctxt_switches", "0")
                ),
            })
        except (IndexError, ValueError):
            continue

    threads.sort(key=lambda thread: thread["tid"])
    return threads