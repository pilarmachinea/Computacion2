"""
procfs.py — helpers para leer y parsear archivos de /proc.
Responsabilidad de este módulo: "saber hablar con el sistema" (Linux /proc).
No sabe nada de multiprocessing, de intervalos de recolección, ni de cómo
se muestra la información — eso es responsabilidad de recolector.py,
cpu.py y los analizadores/.
"""

import os
import pwd
import signal

def _leer_stat_desde_ruta(ruta):
    """
    Parsea un archivo con el formato de /proc/<pid>/stat.
    No agrega pid/tid: eso lo hacen las funciones públicas que sí
    conocen esos valores.

    Nota: sid/pgid son conceptos de PROCESO (y de sesión/grupo de procesos),
    no de thread individual. El kernel repite el mismo valor del proceso
    padre en el stat de cada task/<tid>, pero conceptualmente solo tiene
    sentido interpretarlos a nivel de proceso completo.
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
            "state": resto[0],             # campo 3
            "ppid": int(resto[1]),         # campo 4
            "sid": int(resto[3]),          # campo 6  (session id, a nivel proceso)
            "pgid": int(resto[4]),         # campo 7  (process group id, a nivel proceso)
            "minflt": int(resto[7]),       # campo 10
            "cminflt": int(resto[8]),      # campo 11
            "majflt": int(resto[9]),       # campo 12
            "cmajflt": int(resto[10]),     # campo 13
            "utime": int(resto[11]),       # campo 14
            "stime": int(resto[12]),       # campo 15
            "priority": int(resto[15]),    # campo 18
            "nice": int(resto[16]),        # campo 19
            "rt_priority": int(resto[37]), # campo 40
            "policy": int(resto[38]),      # campo 41
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


def decodificar_mascara_senales(mascara_hex):
    """
    Decodifica una máscara hexadecimal de 64 bits (SigBlk, SigIgn, SigCgt,
    SigPnd, ShdPnd de /proc/<pid>/status) a una lista de nombres de señales.
    """
    mascara = int(mascara_hex, 16)
    nombres = []

    for n in range(1, 65):  # las señales POSIX van de 1 a 64
        if mascara & (1 << (n - 1)):
            try:
                nombres.append(signal.Signals(n).name)
            except ValueError:
                nombres.append(f"SIG{n}")  # número sin nombre estándar conocido

    return nombres

# Políticas del scheduler de Linux. A diferencia de las señales, acá no hay
# un módulo estándar de Python que las traduzca -- son pocas, definidas por
# el kernel, y prácticamente no cambian, así que un diccionario manual es
# la solución más simple y razonable.
POLICIES = {
    0: "SCHED_OTHER",
    1: "SCHED_FIFO",
    2: "SCHED_RR",
    3: "SCHED_BATCH",
    5: "SCHED_IDLE",
    6: "SCHED_DEADLINE",
}

def nombre_policy(policy):
    """Traduce el número de política de scheduling (campo 41 de stat) a su nombre."""
    return POLICIES.get(policy, f"UNKNOWN({policy})")


def listar_pids():
    """Devuelve la lista de PIDs activos en el sistema."""
    pids = []
    for entrada in os.listdir("/proc"):
        if entrada.isdigit():
            pids.append(int(entrada))
    return pids

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


def leer_threads(pid):
    """
    Devuelve una lista con info de cada thread (LWP) del proceso pid:
    tid, comm, state, utime, stime, context switches.
    """
    ruta_task = f"/proc/{pid}/task"
    threads = []

    try:
        entradas = os.listdir(ruta_task)
    except (FileNotFoundError, PermissionError):
        return None

    for entrada in entradas:
        if not entrada.isdigit():
            continue
        tid = int(entrada)

        stat = leer_stat_thread(pid, tid)
        status = leer_status_thread(pid, tid)
        if stat is None or status is None:
            continue

        threads.append({
            "pid": pid,
            "tid": tid,
            "comm": stat["comm"],
            "state": stat["state"],
            "utime": stat["utime"],
            "stime": stat["stime"],
            "voluntary_ctxt_switches": int(status.get("voluntary_ctxt_switches", "0")),
            "nonvoluntary_ctxt_switches": int(status.get("nonvoluntary_ctxt_switches", "0")),
        })

    return threads


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


def senales_de(pid):
    """
    Lee las 5 máscaras de señales de /proc/<pid>/status y las decodifica
    a nombres legibles.
    """
    status = leer_status(pid)
    if status is None:
        return None

    return {
        "pid": pid,
        "bloqueadas": decodificar_mascara_senales(status.get("SigBlk", "0")),
        "ignoradas": decodificar_mascara_senales(status.get("SigIgn", "0")),
        "con_handler": decodificar_mascara_senales(status.get("SigCgt", "0")),
        "pendientes_proceso": decodificar_mascara_senales(status.get("SigPnd", "0")),
        "pendientes_grupo": decodificar_mascara_senales(status.get("ShdPnd", "0")),
    }

def scheduling_de(pid):
    """
    Combina leer_stat + leer_status y devuelve los campos
    tipados que necesita la vista Scheduling.
    """
    stat = leer_stat(pid)
    status = leer_status(pid)

    if stat is None or status is None:
        return None

    return {
        "pid": pid,
        "comm": stat["comm"],
        "sid": stat["sid"],
        "pgid": stat["pgid"],
        "priority": stat["priority"],
        "nice": stat["nice"],
        "rt_priority": stat["rt_priority"],
        "policy": stat["policy"],
        "policy_nombre": nombre_policy(stat["policy"]),
        "cpus_allowed_list": status.get("Cpus_allowed_list", ""),
        "voluntary_ctxt_switches": int(
            status.get("voluntary_ctxt_switches", "0")
        ),
        "nonvoluntary_ctxt_switches": int(
            status.get("nonvoluntary_ctxt_switches", "0")
        ),
        "utime": stat["utime"],
        "stime": stat["stime"],
    }

def leer_stat_global():
    """
    Lee la primera línea ('cpu ...') de /proc/stat: contadores acumulados
    de jiffies del sistema completo, agrupados por categoría.
    """
    with open("/proc/stat", "r") as f:
        primera_linea = f.readline()
    partes = primera_linea.split()
    # partes[0] es la palabra "cpu"; los campos numéricos siguen desde partes[1]
    valores = [int(x) for x in partes[1:]]
    user, nice, system, idle, iowait = valores[0:5]
    return {
        "user": user,
        "nice": nice,
        "system": system,
        "idle": idle,
        "iowait": iowait,
    }

def leer_loadavg():
    """Lee /proc/loadavg: promedios de carga del sistema (1, 5, 15 min)."""
    with open("/proc/loadavg", "r") as f:
        partes = f.read().split()
    return {
        "load_1min": float(partes[0]),
        "load_5min": float(partes[1]),
        "load_15min": float(partes[2]),
    }


def leer_uptime():
    """Lee /proc/uptime: segundos desde el arranque del sistema."""
    with open("/proc/uptime", "r") as f:
        partes = f.read().split()
    return float(partes[0])


def leer_meminfo():
    """Lee /proc/meminfo: memoria total/libre/buffers/cached/swap, en KB."""
    datos = {}
    with open("/proc/meminfo", "r") as f:
        for linea in f:
            clave, _, valor = linea.partition(':')
            datos[clave.strip()] = _kb_a_int(valor.strip())
    return datos

def contar_procesos_por_estado():
    estados = {}
    total = 0
    zombies = 0
    total_threads = 0
    for pid in listar_pids():
        stat = leer_stat(pid)
        status = leer_status(pid)
        if stat is None or status is None:
            continue
        total += 1
        estado = stat["state"]
        if estado in estados:
            estados[estado] += 1
        else:
            estados[estado] = 1
        if estado == "Z":
            zombies += 1
        total_threads += int(status.get("Threads", "0"))
    return {
        "total_procesos": total,
        "por_estado": estados,
        "zombies": zombies,
        "total_threads": total_threads,
    }

def sistema_global(calc_cpu_global):
    """
    Combina todos los datos globales del sistema: CPU%, load average,
    memoria, conteo de procesos, y uptime.
    calc_cpu_global: instancia de CalculadoraCPUGlobal (mantiene estado
    entre llamadas, por eso se pasa desde afuera, no se crea acá adentro).
    """
    stat_cpu = leer_stat_global()
    cpu_pct = calc_cpu_global.calcular(
        stat_cpu["user"], stat_cpu["nice"], stat_cpu["system"],
        stat_cpu["idle"], stat_cpu["iowait"]
    )

    loadavg = leer_loadavg()
    uptime_seg = leer_uptime()
    meminfo = leer_meminfo()
    procesos = contar_procesos_por_estado()

    return {
        "cpu_pct": cpu_pct,
        "loadavg": loadavg,
        "uptime_seg": uptime_seg,
        "mem_total_kb": meminfo.get("MemTotal", 0),
        "mem_libre_kb": meminfo.get("MemFree", 0),
        "mem_buffers_kb": meminfo.get("Buffers", 0),
        "mem_cached_kb": meminfo.get("Cached", 0),
        "swap_total_kb": meminfo.get("SwapTotal", 0),
        "swap_libre_kb": meminfo.get("SwapFree", 0),
        "procesos": procesos,
    }