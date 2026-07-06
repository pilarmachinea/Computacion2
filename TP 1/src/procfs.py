import os
import time
import pwd

def listar_pids():
    """Devuelve la lista de PIDs activos en el sistema."""
    pids = []
    for entrada in os.listdir("/proc"):
        if entrada.isdigit():
            pids.append(int(entrada))
    return pids

class CalculadoraCPU:
    """
    Mantiene el estado de lecturas anteriores por PID
    para calcular CPU% instantáneo entre llamadas.
    """
    def __init__(self):
        self._anteriores = {}
        self._hz = os.sysconf('SC_CLK_TCK')

    def calcular(self, pid, utime, stime):
        ahora = time.time()
        jiffies_totales = utime + stime

        anterior = self._anteriores.get(pid)
        self._anteriores[pid] = (jiffies_totales, ahora)

        if anterior is None:
            return 0.0

        jiffies_prev, t_prev = anterior
        delta_jiffies = jiffies_totales - jiffies_prev
        delta_t = ahora - t_prev

        if delta_t <= 0:
            return 0.0

        return (delta_jiffies / self._hz) / delta_t * 100


def leer_stat(pid):
    ruta = f"/proc/{pid}/stat"
    try:
        with open(ruta, "r") as f:
            linea = f.read().strip()
        fin_comm = linea.rfind(')')
        comm = linea[linea.find('(') + 1:fin_comm]
        resto = linea[fin_comm + 2:].split()
        return {
            "pid": pid,
            "comm": comm,
            "state": resto[0],
            "ppid": int(resto[1]),
            "utime": int(resto[11]),
            "stime": int(resto[12]),
        }
    except FileNotFoundError:
        return None

def leer_stat(pid):
    ruta = f"/proc/{pid}/stat"
    try:
        with open(ruta, "r") as f:
            linea = f.read().strip()
        fin_comm = linea.rfind(')')
        comm = linea[linea.find('(') + 1:fin_comm]
        resto = linea[fin_comm + 2:].split()
        return {
            "pid": pid,
            "comm": comm,
            "state": resto[0],
            "ppid": int(resto[1]),
            "utime": int(resto[11]),
            "stime": int(resto[12]),
        }
    except FileNotFoundError:
        return None


def leer_status(pid):
    ruta = f"/proc/{pid}/status"
    try:
        datos = {}
        with open(ruta, "r") as f:
            for linea in f:
                clave, _, valor = linea.partition(':')
                datos[clave.strip()] = valor.strip()
        return datos
    except FileNotFoundError:
        return None
    
def resumen_de(pid):
    """
    Combina leer_stat + leer_status y devuelve los campos
    tipados que necesita la vista Resumen.
    """
    stat = leer_stat(pid)
    status = leer_status(pid)
    if stat is None or status is None:
        return None

    return {
        "pid": pid,
        "comm": stat["comm"],
        "ppid": stat["ppid"],
        "state": status["State"].split()[0],          # "R (running)" -> "R"
        "threads": int(status["Threads"]),
        "vmrss_kb": _kb_a_int(status.get("VmRSS", "0 kB")),
        "uid": status["Uid"].split()[0],               # primer UID = real
        "utime": stat["utime"],
        "stime": stat["stime"],
    }


def _kb_a_int(texto):
    """'9984 kB' -> 9984"""
    return int(texto.split()[0])

def leer_cmdline(pid):
    ruta = f"/proc/{pid}/cmdline"
    try:
        with open(ruta, "rb") as f:
            data = f.read()
        if not data:
            return ""
        partes = data.split(b'\x00')
        return ' '.join(p.decode(errors='replace') for p in partes if p)
    except FileNotFoundError:
        return None
    
def usuario_de(uid):
    """Traduce un UID numérico a nombre de usuario. Si no existe, devuelve el UID como string."""
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def resumen_de(pid):
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
