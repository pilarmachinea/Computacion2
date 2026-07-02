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