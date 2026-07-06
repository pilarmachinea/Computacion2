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