from procfs import leer_stat, leer_status, resumen_de, _kb_a_int
import os

print(leer_stat(os.getpid()))
print(leer_status(os.getpid()))
print(resumen_de(os.getpid()))