from procfs import sistema_global
from cpu import CalculadoraCPUGlobal

calc = CalculadoraCPUGlobal()
print(sistema_global(calc))

import time
print(sistema_global(calc))
time.sleep(2)
print(sistema_global(calc))