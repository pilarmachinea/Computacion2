import os
import time

class CalculadoraCPU:
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
    
class CalculadoraCPUGlobal:
    """
    Calcula el % de CPU global del sistema (user/system/idle/iowait)
    a partir de dos lecturas de /proc/stat separadas en el tiempo.
    """
    def __init__(self):
        self._anterior = None  

    def calcular(self, user, nice, system, idle, iowait):
        actual = (user, nice, system, idle, iowait)

        if self._anterior is None:
            self._anterior = actual
            return {"user": 0.0, "system": 0.0, "idle": 0.0, "iowait": 0.0}

        deltas = [actual[i] - self._anterior[i] for i in range(5)]
        total = sum(deltas)
        self._anterior = actual

        if total <= 0:
            return {"user": 0.0, "system": 0.0, "idle": 0.0, "iowait": 0.0}

        d_user, d_nice, d_system, d_idle, d_iowait = deltas
        return {
            "user": (d_user + d_nice) / total * 100,
            "system": d_system / total * 100,
            "idle": d_idle / total * 100,
            "iowait": d_iowait / total * 100,
        }