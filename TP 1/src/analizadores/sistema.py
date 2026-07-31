import time
from procfs import sistema_global
from cpu import CalculadoraCPUGlobal


def analizador_sistema_loop(vista_compartida, intervalo=2):
    """
    A diferencia de los demás analizadores, esta vista no tiene "filas por
    proceso" -- es un solo diccionario con el estado global del sistema.
    Por eso vista_compartida es un Manager().dict(), no un Manager().list().
    """
    calc_cpu_global = CalculadoraCPUGlobal()

    while True:
        datos = sistema_global(calc_cpu_global)
        vista_compartida.update(datos)
        time.sleep(intervalo)