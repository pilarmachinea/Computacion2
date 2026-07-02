from procfs import leer_stat, CalculadoraCPU
import time
import os

calc = CalculadoraCPU()
pid = os.getpid()

for i in range(3):
    stat = leer_stat(pid)
    cpu = calc.calcular(pid, stat["utime"], stat["stime"])
    print(f"vuelta {i}: cpu% = {cpu:.2f}")
    time.sleep(1)
    # generamos algo de carga para que se note
    sum(range(10_000_000))