import time
from procfs import sistema_global
from cpu import CalculadoraCPUGlobal

def analizador_sistema_loop(vista_compartida, config):
    calc_cpu_global = CalculadoraCPUGlobal()

    while True:
        datos = sistema_global(calc_cpu_global)

        vista_compartida.clear()
        vista_compartida.update(datos)

        intervalo = config["intervalos"].get("sistema", 2)
        time.sleep(intervalo)