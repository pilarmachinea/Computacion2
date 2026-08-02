# Notas teóricas y bitácora — TP1 Monitor de Procesos y Threads

> Este archivo combina el avance de código con los conceptos teóricos de la materia
> que fueron apareciendo. Sirve como material de repaso para el final y como base
> para las secciones "Decisiones de diseño" y "Conceptos del curso aplicados" del
> README final.

---

## Sesión 1 — Exploración de `/proc` y primeros helpers en `procfs.py`

### Qué hicimos

- Exploramos `/proc/$$/status` y `/proc/$$/stat` a mano desde la terminal.
- Implementamos `leer_stat(pid)`: lee y parsea `/proc/<pid>/stat`.
- Implementamos `leer_status(pid)`: lee y parsea `/proc/<pid>/status` (formato clave-valor).
- Implementamos `resumen_de(pid)`: combina las dos anteriores y devuelve los campos
  tipados que necesita la vista Resumen (`pid`, `comm`, `ppid`, `state`, `threads`,
  `vmrss_kb`, `uid`, `utime`, `stime`).
- Implementamos `leer_cmdline(pid)`: lee `/proc/<pid>/cmdline` (separado por bytes nulos,
  no por espacios).
- Diseñamos (sin implementar todavía) el cálculo de **CPU%** a partir de dos lecturas
  de `utime`/`stime` separadas en el tiempo.
- Creamos el repo en GitHub, con `.gitignore` para `__pycache__/`, `*.pyc`, `venv/`.

### Conceptos teóricos

**Estados de proceso (`R`, `S`, `D`, `T`, `Z`) y `/proc` como foto instantánea**

`/proc` no da promedios ni tendencias: cada lectura es el estado exacto del proceso
en ese instante. Un proceso puede pasar el 99% del tiempo en `S` (sleeping, esperando
I/O — por ejemplo un shell esperando que el usuario tipee) y aparecer en `R` (running)
solo en el microsegundo exacto en que estaba usando la CPU cuando lo leímos. Verificado
en vivo: `bash` esperando input dio `S`; Python ejecutando activamente la línea que
lo leía dio `R`.

**UIDs múltiples: real, effective, saved-set, filesystem**

`/proc/<pid>/status` expone 4 UIDs, no uno solo:
- **Real (RUID)**: quién lanzó realmente el proceso. No cambia con `sudo`.
- **Effective (EUID)**: el que el kernel usa para chequear permisos en el momento.
  Con `sudo`, pasa a 0 (root).
- **Saved-set (SUID)**: guarda un privilegio "de reserva" para que el proceso pueda
  recuperar permisos elevados después de bajarlos temporalmente.
- **Filesystem (FSUID)**: UID usado específicamente para chequeos de permisos sobre
  archivos. Históricamente pensado para servidores NFS que necesitan actuar "como"
  cierto usuario sin heredar todos sus privilegios (ej. no poder mandarle señales).
  En la práctica, casi siempre sigue al EUID.

Mantener el RUID separado del EUID permite auditoría: el sistema siempre sabe quién
autenticó la acción, aunque el proceso esté corriendo con privilegios elevados.

**Formato de `/proc/<pid>/stat` y por qué `split(' ')` ingenuo falla**

Es una sola línea con ~52 campos separados por espacio. El campo 2 es `comm` (nombre
del proceso) **entre paréntesis**: `51066 (bash) S 51056 ...`. El problema: el nombre
de un proceso puede contener espacios (e incluso paréntesis), por lo que un
`linea.split(' ')` ingenuo correría los índices de todos los campos siguientes.

Solución robusta: buscar el **último** `)` de la línea con `rfind(')')` (es seguro
porque no hay más paréntesis después del nombre), tomar todo lo anterior como
`comm`, y solo la parte posterior al `)` separarla por espacios con `.split()`.

Mapeo de índices: si el enunciado numera los campos desde 1 (con `pid`=campo 1,
`comm`=campo 2), y la lista `resto` empieza después del `comm`, entonces:
`índice_en_resto = campo_del_enunciado − 3`.
Ejemplo: `utime` (campo 14) → `resto[11]`; `stime` (campo 15) → `resto[12]`.

**Manejo de procesos que desaparecen entre listarlos y leerlos**

Es un caso esperado, no un error del programa: el recolector lista PIDs, pero para
cuando se llega a leer el `/proc/<pid>/...` de alguno, ese proceso ya pudo haber
terminado y el archivo desapareció. Se captura `FileNotFoundError` y se devuelve
`None`, dejando que el llamador simplemente ignore ese proceso y siga con los demás
— no debe hacer explotar el monitor.

**`/proc/<pid>/status`: formato clave-valor**

A diferencia de `stat`, es texto plano legible: una línea por dato, `Clave:\tValor`.
Se parsea con `linea.partition(':')` línea por línea, sin trampas de espacios.
Los valores numéricos con unidad (ej. `VmRSS: 9984 kB`) quedan como string y hay
que limpiarlos aparte cuando se necesitan como número (`_kb_a_int`).

**`/proc/<pid>/cmdline`: separado por bytes nulos**

A diferencia de `stat` y `status`, los argumentos del comando no están separados por
espacios sino por bytes `\x00`. Hay que abrir el archivo en modo binario (`"rb"`) y
hacer `data.split(b'\x00')`. Si el proceso es un kernel thread o no tiene argv, el
archivo puede estar vacío.

**CPU%: por qué no es un valor directo, sino un delta**

`utime` y `stime` (campos 14 y 15 de `stat`) son contadores **acumulados** de jiffies
(ticks de CPU) desde que el proceso arrancó — nunca bajan, solo suman. Para obtener
un porcentaje instantáneo de uso de CPU hace falta:

1. Tomar dos lecturas de `utime + stime`, separadas por un intervalo real de tiempo (Δt).
2. Calcular cuánto creció ese acumulado entre las dos lecturas (Δjiffies).
3. Convertir Δjiffies a segundos de CPU usados, dividiendo por `HZ`
   (jiffies por segundo del kernel, obtenible con `os.sysconf('SC_CLK_TCK')`,
   típicamente 100).
4. Dividir por Δt real transcurrido y multiplicar por 100:

```
cpu% = (Δ(utime + stime) / HZ) / Δt_real * 100
```

**Decisión de diseño: la "lectura anterior" va en memoria local, no compartida**

El analizador de Resumen necesita recordar, por PID, la última lectura de
utime/stime y el timestamp de esa lectura, para poder calcular el delta en la
siguiente vuelta. Esto se guarda en un **diccionario Python normal, local al proceso
analizador** — no en `Manager.dict` ni memoria compartida. Regla general que vale
para todo el TP: **memoria compartida es solo para lo que otros procesos necesitan
leer** (el resultado final que va al snapshot global). Datos intermedios que solo
usa el propio analizador para su cálculo interno se quedan en memoria local: es
más simple, más rápido, y no necesita locks.

Caso borde: la primera vez que se ve un PID nuevo no hay lectura anterior con la
cual comparar — se le asigna CPU% = 0.0 y se espera a la próxima vuelta del loop.

### Conexión con la materia

- **Clase 3 (Procesos — Fundamentos)**: anatomía de un proceso, `/proc` como
  filesystem virtual que expone el estado del kernel; estados de proceso.
- **Clase 4 (fork, exec, wait)**: jerarquía de procesos (PID/PPID), por qué todo
  proceso salvo `init` tiene padre.

### Código actual (`src/procfs.py`)

```python
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


def _kb_a_int(texto):
    """'9984 kB' -> 9984"""
    return int(texto.split()[0])


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
        "state": status["State"].split()[0],
        "threads": int(status["Threads"]),
        "vmrss_kb": _kb_a_int(status.get("VmRSS", "0 kB")),
        "uid": status["Uid"].split()[0],
        "utime": stat["utime"],
        "stime": stat["stime"],
    }


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
```
---

## Sesión 2 — Cálculo de CPU% (`CalculadoraCPU`)

### Qué hicimos

- Implementamos `CalculadoraCPU`, una clase que mantiene en memoria el estado
  de lecturas anteriores por PID (`{pid: (jiffies_totales, timestamp)}`) para
  poder calcular el CPU% instantáneo a partir de dos lecturas separadas en
  el tiempo.
- Probamos en vivo con un loop de 3 iteraciones + carga artificial de CPU
  (`sum(range(10_000_000))`): la primera vuelta dio `0.00%` (caso borde sin
  lectura anterior) y las siguientes dieron `~10.7%` de forma consistente.
- Primer commit y push a GitHub del repo.

### Conceptos teóricos

**Por qué una clase con estado, y no una función pura**

`CalculadoraCPU` necesita "recordar" entre llamadas la última lectura de
`utime + stime` de cada PID. Una función pura de Python no puede retener
estado entre invocaciones por sí sola (salvo trucos como atributos de función
o `nonlocal`, menos prolijos). Encapsulando el diccionario `_anteriores` como
atributo de instancia (`self._anteriores`), cada analizador que necesite CPU%
crea **una sola instancia** al arrancar su loop, y la reutiliza en cada vuelta
sin tener que pasar el diccionario como parámetro de un lado a otro.

**`HZ` / `SC_CLK_TCK`: la unidad de los jiffies**

`utime` y `stime` están expresados en *jiffies* (ticks del kernel), no en
segundos. La cantidad de jiffies por segundo (`HZ`) depende de la configuración
del kernel — típicamente 100 en sistemas Linux modernos, pero no hay que
asumirlo como constante fija: se obtiene en tiempo de ejecución con
`os.sysconf('SC_CLK_TCK')`, que es la forma portable y correcta de consultarlo.

**La fórmula completa, paso a paso**

```
cpu% = (Δ(utime + stime) / HZ) / Δt_real * 100
```

1. `Δ(utime + stime)`: cuánto creció el acumulado de tiempo de CPU (user + sistema)
   entre dos lecturas.
2. Dividido por `HZ`: convierte esa cantidad de jiffies a **segundos reales de CPU
   usados** en ese intervalo.
3. Dividido por `Δt_real` (segundos de reloj transcurridos entre las dos lecturas):
   da la **fracción** de ese intervalo que el proceso estuvo usando CPU.
4. Multiplicado por 100: porcentaje.

Verificado en vivo: con `sleep(1)` entre lecturas y una carga de CPU que tardó
una fracción de ese segundo, el resultado (~10.7%) es coherente con "el proceso
usó CPU activamente una porción chica del segundo transcurrido".

**Caso borde: primera lectura de un PID**

No hay lectura anterior con la cual comparar, así que no se puede calcular
delta — se devuelve `0.0` y recién en la **segunda** vez que se ve ese PID
el cálculo empieza a tener sentido. Esto es inevitable: todo cálculo de
"tasa" (rate) necesita al menos dos muestras.

### Conexión con la materia

- **Clase 3 / 10 (Procesos, Scheduler)**: `utime`/`stime` son contadores que
  mantiene el kernel por proceso, incrementados cada vez que el scheduler le
  da CPU a ese proceso (en modo usuario o modo kernel respectivamente). Son
  contadores acumulativos de toda la vida del proceso, no valores instantáneos
  — de ahí la necesidad del delta.

### Código actual (agregado a `src/procfs.py`)

```python
import os
import time

class CalculadoraCPU:
    """
    Mantiene el estado de lecturas anteriores por PID
    para calcular CPU% instantáneo entre llamadas.
    """
    def __init__(self):
        self._anteriores = {}  # pid -> (jiffies_totales, timestamp)
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
```
---

## Sesión 3 — Recolector: `listar_pids`, reorganización en módulos, y `recolector_loop`

### Qué hicimos

- Implementamos `listar_pids()`: recorre `/proc` con `os.listdir`, filtra con
  `isdigit()` para quedarse solo con las carpetas numéricas (PIDs), descartando
  entradas como `self`, `cpuinfo`, `meminfo`, `net`, etc.
- Reorganizamos el código en módulos según responsabilidad:
  - `procfs.py`: helpers de lectura/parseo de `/proc` (`leer_stat`, `leer_status`,
    `resumen_de`, `leer_cmdline`, `listar_pids`).
  - `cpu.py`: `CalculadoraCPU` (se movió acá desde `procfs.py`, porque es un
    cálculo derivado, no lectura cruda).
  - `recolector.py`: `recolector_loop`, el loop periódico que orquesta todo.
- Implementamos `recolector_loop(snapshot_compartido, intervalo)`: en cada vuelta,
  lista PIDs, lee `resumen_de` de cada uno, calcula `cpu_pct`, y **actualiza**
  (no reasigna) el diccionario compartido.
- Probamos el loop con un `threading.Thread` (atajo válido para probar sin la
  complejidad de `multiprocessing` todavía, porque un thread comparte memoria
  directo con el proceso que lo lanza). Confirmado con datos reales: el propio
  proceso de prueba mostró `cpu_pct` distinto de cero en la segunda vuelta,
  mientras que procesos en reposo (`systemd`, `kthreadd`) dieron `0.0` real
  (no por ser primera lectura, sino porque genuinamente no consumieron CPU).

### Conceptos teóricos

**Por qué `Manager` y no `Queue`/`Pipe` para este caso**

`Queue` y `Pipe` son mecanismos de **paso de mensajes**: un dato se envía, un
consumidor lo recibe, y ese mensaje se consume (desaparece de la cola). Son
ideales para "un productor, un consumidor" o "reparto de tareas" (varios
consumidores compiten por los mismos ítems, cada ítem lo procesa solo uno).

El caso del Recolector es distinto: **un productor (Recolector) que genera un
snapshot completo cada N segundos, y varios consumidores (los 7 analizadores)
que necesitan leer el mismo snapshot repetidamente**, no consumirlo una sola
vez. `Manager` resuelve esto dando un objeto compartido (ej. `dict`) que
persiste entre lecturas — más parecido a un "pizarrón" que todos pueden mirar
cuantas veces quieran, que a una cola de mensajes.

**Mutación vs. reasignación de una referencia (clave para trabajar con
`Manager` u objetos compartidos en general)**

Concepto base de Python, sin multiprocessing todavía: cuando una función recibe
un objeto mutable (lista, dict) como parámetro, la variable local es una
**referencia** a ese mismo objeto. Hay dos formas muy distintas de "modificarlo"
dentro de la función:

- **Mutar** (`snapshot.update(...)`, `lista.append(...)`): cambia el *contenido*
  del objeto al que la referencia apunta. Visible desde afuera de la función,
  porque quien llamó también tiene una referencia al mismo objeto.
- **Reasignar** (`snapshot = {}`, `lista = [1,2,3]`): hace que la variable
  *local* apunte a un objeto **nuevo**. La variable de quien llamó a la función
  sigue apuntando al objeto viejo — el cambio es invisible desde afuera.

Aplicado al Recolector: si `recolector_loop` hiciera
`snapshot_compartido = nuevo_snapshot`, estaría reemplazando la referencia local
por un dict Python normal nuevo, perdiendo la conexión con el `Manager().dict()`
real que los analizadores están mirando. Por eso la implementación usa
`.update(...)`, que muta el objeto compartido existente en su lugar.

**`isdigit()` para filtrar PIDs válidos**

`/proc` mezcla carpetas numéricas (una por proceso vivo, nombradas con su PID)
con archivos/carpetas de nombre fijo que exponen info global del sistema
(`cpuinfo`, `meminfo`, `net`, `self`, `version`, etc.). `entrada.isdigit()`
devuelve `True` solo si **todos** los caracteres del string son dígitos,
lo cual filtra correctamente las carpetas de proceso.

Nota metodológica: al probar con `ls /proc | head -30` parecía que "todo era
numérico", pero es un efecto del orden alfabético de `ls` (los dígitos ordenan
antes que las letras en ASCII) — las entradas no numéricas estaban más abajo
en la lista, visibles recién con `tail -30`.

**Diseño en capas: separar lectura cruda, cálculo derivado, y orquestación**

- `procfs.py` no sabe nada de "cada cuánto" se recolecta ni de multiprocessing
  — solo sabe leer un dato puntual de `/proc` dado un PID.
- `cpu.py` no sabe leer `/proc` — solo sabe calcular una tasa a partir de dos
  mediciones que le pasan.
- `recolector.py` no sabe parsear `/proc` ni calcular la fórmula de CPU% —
  solo orquesta: pide datos, junta resultados, actualiza el snapshot compartido.

Esta separación (cada módulo con una sola responsabilidad) va a facilitar
testear cada pieza por separado y explicar el diseño en la defensa oral.

### Conexión con la materia

- **Clase 5 (Pipes) / Clase 7-8 (multiprocessing)**: diferencia entre mecanismos
  de paso de mensajes (`Queue`, `Pipe`) y memoria compartida gestionada
  (`Manager`), y cuándo conviene cada uno según el patrón de comunicación
  (productor-consumidor único vs. productor-múltiples-lectores).

### Código actual

**`src/cpu.py`**
```python
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
```

**`src/procfs.py`** — se le agregó `listar_pids`, se le sacó `CalculadoraCPU`
(ver sesión 2 para el resto de las funciones, que no cambiaron):
```python
def listar_pids():
    """Devuelve la lista de PIDs activos en el sistema."""
    pids = []
    for entrada in os.listdir("/proc"):
        if entrada.isdigit():
            pids.append(int(entrada))
    return pids
```

**`src/recolector.py`** (nuevo)
```python
import time
from procfs import listar_pids, resumen_de
from cpu import CalculadoraCPU


def recolector_loop(snapshot_compartido, intervalo=2):
    """
    snapshot_compartido: un Manager().dict() pasado desde main.py
    intervalo: segundos entre cada recolección
    """
    calc_cpu = CalculadoraCPU()

    while True:
        pids = listar_pids()
        nuevo_snapshot = {}

        for pid in pids:
            datos = resumen_de(pid)
            if datos is None:
                continue
            datos["cpu_pct"] = calc_cpu.calcular(pid, datos["utime"], datos["stime"])
            nuevo_snapshot[pid] = datos

        snapshot_compartido.update(nuevo_snapshot)
        time.sleep(intervalo)
```
---

## Sesión 4 — `main.py` real, fork vs spawn, y primer analizador (Resumen) funcionando

### Qué hicimos

- Armamos `main.py`: crea el `Manager()` real, lanza el Recolector como
  `multiprocessing.Process` (no `Thread` de prueba), lo deja correr, imprime
  resultados, y lo cierra prolijamente con `terminate()` + `join()`.
- Completamos `resumen_de` con los campos que faltaban según la consigna:
  `gid`, `usuario` (nombre resuelto desde el UID), y `cmdline` (comando completo,
  con fallback a `[comm]` para kernel threads sin argv).
- Agregamos el helper `usuario_de(uid)` en `procfs.py`, usando el módulo `pwd`.
- Armamos el primer analizador real: `analizadores/resumen.py` con
  `analizador_resumen_loop`, que lee el snapshot del Recolector, lo ordena por
  `cpu_pct`, y escribe el resultado a una `Manager().list()` compartida.
- Corrimos Recolector + analizador de Resumen **juntos**, cada uno como su
  propio proceso, comunicados exclusivamente a través de objetos `Manager`.
  Resultado verificado en vivo: la vista mostró correctamente los procesos de
  mayor CPU% (VS Code, Firefox) ordenados de mayor a menor.

### Conceptos teóricos

**`fork` vs `spawn`, y por qué `Manager` funciona igual con cualquiera de los dos**

`fork` (default en Linux): el proceso hijo nace como copia del padre en el
instante del `fork()`, heredando todo el estado de memoria vía copy-on-write.
`spawn`: se arranca un intérprete de Python nuevo y limpio, que vuelve a
importar el módulo desde cero — no hereda memoria del padre.

Un `Manager().dict()` (o `.list()`) **no es** un objeto que viva en la memoria
del proceso padre: es un **proxy** serializable (picklable) que se comunica,
vía IPC interno (sockets/pipes), con un proceso servidor separado que el
`Manager` levanta automáticamente. Por eso da igual si el hijo nace por `fork`
o `spawn`: en ambos casos recibe el proxy como argumento normal de la función,
y ese proxy sabe conectarse al mismo servidor sin importar cómo nació el
proceso. Esto es justo lo que hace a `Manager` la opción "seguridad por
defecto" frente a `Value`/`Array` (memoria compartida real vía `mmap`, que sí
es más sensible al método de arranque).

**El guard `if __name__ == "__main__":`**

Necesario siempre que se usa `multiprocessing`, especialmente crítico con
`spawn`: si el archivo se reimporta desde el proceso hijo (que es lo que hace
`spawn` al arrancar), sin el guard el código de nivel superior (crear más
procesos) se ejecutaría de nuevo dentro del hijo, generando un bucle
descontrolado de creación de procesos. Con `fork` en Linux este bug específico
no ocurre (no hay reimportación, solo copia de memoria), pero se mantiene el
guard igual por portabilidad y porque no se puede asumir con certeza qué
método de arranque usa la imagen base de Python dentro del contenedor Docker
del TP.

**Ciclo de vida ordenado de procesos: `daemon=True` + `terminate()` + `join()`**

`daemon=True` asegura que el proceso hijo no quede huérfano si el padre muere
inesperadamente. Pero además conviene cerrar explícitamente con `terminate()`
(pedido de cierre) seguido de `join()` (esperar la confirmación de que
efectivamente terminó) antes de que el proceso principal siga o finalice.
Sin el `join()`, el intérprete podría empezar a cerrarse mientras el hijo
todavía está escribiendo en el `Manager` (que depende de su propio proceso
servidor, también cerrándose), generando errores como `BrokenPipeError` al
salir aunque el programa haya "funcionado bien". `join()` da determinismo:
no se avanza hasta confirmar que el hijo terminó limpio.

**`Manager().list()` y reemplazo de contenido vía slicing**

Para vistas que necesitan **orden** (como Resumen, rankeada por CPU%), un
`Manager().dict()` no sirve — los dicts no garantizan orden útil para ranking.
Se usa `Manager().list()` en su lugar. El mismo problema de mutación vs.
reasignación aplica acá: para reemplazar **todo** el contenido de la lista
compartida por una nueva lista ordenada sin romper la conexión al proxy
compartido, se usa slicing asignable: `vista_compartida[:] = filas_ordenadas`,
que reemplaza el contenido del objeto existente en lugar de crear uno nuevo
(equivalente en efecto a `.update()` sobre un dict).

**Por qué copiar valores compartidos antes de iterarlos (`list(dict.values())`)**

Iterar directamente sobre `.values()` de un `Manager().dict()` mientras otro
proceso podría estar modificándolo al mismo tiempo es riesgoso: puede generar
inconsistencias o errores de iteración sobre una colección cambiante. Se
soluciona congelando una copia local (`list(snapshot.values())`) antes de
procesarla — el analizador trabaja sobre una foto consistente del instante en
que la tomó, sin importar qué haga el Recolector después.

**`pwd.getpwuid` y manejo de UIDs sin usuario asociado**

`pwd.getpwuid(uid).pw_name` traduce un UID numérico al nombre de usuario
consultando la base de usuarios del sistema (`/etc/passwd` o la fuente NSS
configurada). Puede lanzar `KeyError` si el UID no tiene entrada asociada
—común en procesos dentro de contenedores Docker, UIDs remapeados, o usuarios
eliminados con procesos aún corriendo—. Se captura ese caso y se devuelve el
UID como string, priorizando que el monitor siga mostrando información útil
en vez de fallar.

**Frecuencias de actualización distintas entre Recolector y analizador**

No tienen por qué compartir el mismo intervalo: el Recolector hace el trabajo
"caro" de leer `/proc` y debería correr más seguido; el analizador solo lee,
transforma y ordena datos ya recolectados, por lo que puede refrescar un poco
más espaciado sin perder actualidad. (Regla general, no absoluta: un
analizador con cálculos pesados —ej. promedios históricos— podría justificar
lo inverso.)

### Conexión con la materia

- **Clase 8-9 (Multiprocessing)**: `fork`/`spawn`/`forkserver` como estrategias
  de arranque de proceso, y sus implicancias sobre qué hereda o no el hijo del
  padre; ciclo de vida de un proceso (`start`, `terminate`, `join`) y por qué
  importa el orden de esas llamadas.

### Código actual

**`src/main.py`**
```python
import time
import multiprocessing as mp

from recolector import recolector_loop
from analizadores.resumen import analizador_resumen_loop


def main():
    manager = mp.Manager()
    snapshot = manager.dict()
    vista_resumen = manager.list()

    p_recolector = mp.Process(
        target=recolector_loop, args=(snapshot, 1), daemon=True
    )
    p_resumen = mp.Process(
        target=analizador_resumen_loop, args=(snapshot, vista_resumen, 2), daemon=True
    )

    p_recolector.start()
    p_resumen.start()

    time.sleep(6)

    print(f"Filas en vista_resumen: {len(vista_resumen)}")
    print("Top 3 por CPU%:")
    for fila in vista_resumen[:3]:
        print(f"  PID {fila['pid']:>6}  {fila['comm']:<20}  CPU {fila['cpu_pct']:.1f}%  usuario={fila['usuario']}")

    p_recolector.terminate()
    p_resumen.terminate()
    p_recolector.join()
    p_resumen.join()


if __name__ == "__main__":
    main()
```

**`src/analizadores/resumen.py`**
```python
import time


def analizador_resumen_loop(snapshot_recolector, vista_compartida, intervalo=2):
    while True:
        filas = list(snapshot_recolector.values())
        filas_ordenadas = sorted(filas, key=lambda p: p["cpu_pct"], reverse=True)
        vista_compartida[:] = filas_ordenadas
        time.sleep(intervalo)
```

**`src/procfs.py`** — agregado `usuario_de`, y `resumen_de` ahora incluye
`gid`, `usuario`, `cmdline` (ver código completo en sesiones anteriores para
`leer_stat`, `leer_status`, `_kb_a_int`, `listar_pids`, `leer_cmdline`, que no
cambiaron):
```python
import pwd

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
```
---

## Sesión 5 — Vista Memoria: page faults y `/proc/<pid>/maps`

### Qué hicimos

- Ampliamos `leer_stat` para incluir `minflt`, `cminflt`, `majflt`, `cmajflt`
  (campos 10-13 de `/proc/<pid>/stat`), usando la misma fórmula de mapeo de
  índices ya conocida (`índice_en_resto = campo − 3`).
- Implementamos `leer_maps(pid)`: parsea `/proc/<pid>/maps`, calcula el tamaño
  de cada segmento de memoria virtual a partir del rango de direcciones
  (hex), y agrupa el total en 5 categorías: `text`, `data`, `heap`, `stack`,
  `shared`.
- Separamos la lógica de clasificación en `_clasificar_segmento(path,
  permisos)`, aparte del loop de lectura, para facilitar testeo y extensión.
- Agregamos `_bytes_a_legible(cantidad_bytes)` como helper de formateo,
  dejando explícitamente los datos crudos (bytes) en `procfs.py` y el
  formateo para humanos como paso posterior, en el analizador.
- Probado en vivo sobre el propio proceso de Python: valores coherentes
  (`data` el más grande por las librerías `.so` cargadas, `heap` chico para
  un proceso recién arrancado, `stack` ~132 KB, típico de un solo thread).

### Conceptos teóricos

**Minor vs. major page fault**

Ambos ocurren cuando un proceso accede a una dirección de memoria virtual
que su tabla de páginas todavía no tiene mapeada, pero se resuelven de forma
muy distinta:

- **Minor fault**: la página física ya está en RAM (por ejemplo, porque otro
  proceso ya la cargó, o el kernel la tenía cacheada), y el kernel solo
  necesita actualizar las tablas de páginas del proceso para apuntar a ella.
  No hay I/O involucrado — es relativamente barato.
- **Major fault**: la página no está en RAM, y el kernel tiene que traerla
  desde disco o desde swap. Esto implica una operación de I/O real, mucho
  más lenta, y el proceso queda bloqueado esperando esa lectura.

Por qué importa distinguirlos en el monitor: un proceso con muchos *major*
faults es una señal de presión real de memoria (el sistema está recurriendo
a swap, o cargando datos de disco constantemente) — un síntoma de
rendimiento mucho más serio que los minor faults, que son normales y
esperables durante la ejecución de casi cualquier programa.

**`/proc/<pid>/maps`: anatomía de una línea**

Formato: `dir_inicio-dir_fin permisos offset dispositivo inodo [path]`.

- **`dir_inicio-dir_fin`** (hexadecimal): rango de direcciones virtuales que
  ocupa ese segmento en el espacio de direcciones del proceso.
- **`permisos`** (ej. `r-xp`): lectura/escritura/ejecución del segmento, más
  una `p`/`s` indicando si el mapeo es privado (copy-on-write, cambios no se
  comparten) o compartido. El código (`.text`) va con `r-x` pero **sin** `w`
  — protección conocida como W^X (write xor execute): un segmento nunca debe
  ser simultáneamente escribible y ejecutable, para dificultar que un bug o
  ataque modifique instrucciones en ejecución.
- **`path`** (opcional): si el segmento está respaldado por un archivo
  (binario, librería `.so`), aparece su ruta. Puede faltar por completo en
  líneas de memoria anónima, o mostrar una etiqueta especial como `[heap]` o
  `[stack]`.

**Por qué un mismo binario aparece fragmentado en varias líneas**

Un ejecutable (formato ELF) está compuesto de varias secciones con
propósitos distintos: código (`.text`), datos de solo lectura (`.rodata`),
datos modificables (`.data`/`.bss`). El kernel las mapea como segmentos
separados, cada uno con el permiso mínimo necesario según su función —
aplicación directa del principio de menor privilegio.

**Regiones anónimas: `[heap]` vs. memoria anónima sin etiqueta**

Ambas son memoria sin archivo de respaldo detrás (creada directamente por el
kernel, no cargada desde disco), pero `[heap]` es específicamente la región
contigua clásica que crece con `brk()`/`sbrk()` para asignaciones dinámicas
(`malloc`, `new`). Otras regiones anónimas sin etiquetar suelen usarse para
mapeos adicionales que las librerías (o el propio runtime del lenguaje) piden
directamente con `mmap` anónimo, fuera del heap tradicional.

**Parseo de rangos hexadecimales**

`int(texto, 16)` interpreta el string como número en base 16 (hexadecimal).
Sin el segundo argumento, Python asumiría base 10 y fallaría con
`ValueError` ante cualquier letra `a-f`, o directamente daría un valor
numérico incorrecto si por coincidencia el string solo tuviera dígitos.
El tamaño de cada segmento se obtiene restando `dir_fin − dir_inicio` ya
convertidos a enteros.

**Separación entre datos crudos y formateo para humanos**

`leer_maps` devuelve bytes crudos (enteros), no strings ya formateados como
`"5.3 MB"`. La razón: los números crudos se pueden sumar, ordenar y comparar
directamente (por ejemplo, sumar el `data` de todos los procesos para una
vista Sistema, u ordenar procesos por tamaño de heap) — algo que un string
ya formateado no permite sin volver a parsearlo. El formateo (`_bytes_a_legible`)
se aplica recién en el paso final, cuando el dato ya no necesita seguir
siendo operado, típicamente en el analizador o en `display.py`. Es la misma
regla de diseño que ya se venía aplicando: los módulos internos manejan
datos "puros"; el formateo para humanos es lo último que pasa.

### Conexión con la materia

- **Clase de memoria virtual**: page faults (minor/major), demand paging,
  y el rol de la tabla de páginas.
- **Clase de estructura de procesos / ELF**: segmentación del espacio de
  direcciones de un proceso (text/data/heap/stack), y por qué cada segmento
  tiene permisos distintos.

### Código actual (agregado a `src/procfs.py`)

```python
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
            "minflt": int(resto[7]),
            "cminflt": int(resto[8]),
            "majflt": int(resto[9]),
            "cmajflt": int(resto[10]),
            "utime": int(resto[11]),
            "stime": int(resto[12]),
        }
    except FileNotFoundError:
        return None


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
    except FileNotFoundError:
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


def _bytes_a_legible(cantidad_bytes):
    """1536000 -> '1.5 MB'"""
    for unidad in ['B', 'KB', 'MB', 'GB']:
        if cantidad_bytes < 1024:
            return f"{cantidad_bytes:.1f} {unidad}"
        cantidad_bytes /= 1024
    return f"{cantidad_bytes:.1f} TB"
```
---

## Sesión 7 — Vista Señales: decodificación de máscaras hexadecimales de 64 bits

### Qué hicimos

- Implementamos `decodificar_mascara_senales(mascara_hex)`: convierte un
  string hexadecimal (`SigBlk`, `SigIgn`, `SigCgt`, `SigPnd`, `ShdPnd` de
  `/proc/<pid>/status`) a una lista de nombres de señales legibles, usando
  operaciones a nivel de bits (`&`, `<<`) y el módulo `signal` de Python
  para traducir número → nombre (en vez de mantener un diccionario a mano).
- Validamos la función a mano, no solo confiando en el resultado: convertimos
  el hex a binario con `bin(int(...))`, contamos manualmente las posiciones
  de los bits prendidos, y confirmamos que correspondían exactamente a
  `SIGPIPE` (bit 12 → señal 13) y `SIGXFSZ` (bit 24 → señal 25).
- Implementamos `senales_de(pid)`: combina las 5 máscaras ya decodificadas
  en un solo diccionario por proceso.
- `analizadores/senales.py`: mismo patrón que los anteriores, con una
  diferencia importante — esta vista **no tiene un criterio de orden
  significativo** (a diferencia de CPU%, RSS, cantidad de FDs/threads), así
  que se ordena por PID ascendente, explícitamente con `.sort()`, en vez de
  confiar en el orden no garantizado que devuelve `listar_pids()`.
- Corrido en conjunto con los demás analizadores: datos reales mostraron un
  contraste revelador entre `systemd` (PID 1, ignora solo `SIGPIPE`) y
  `kthreadd` (PID 2, ignora prácticamente el catálogo completo, incluidas
  señales de tiempo real) — buen ejemplo concreto para el README/defensa.

### Conceptos teóricos

**Máscaras de bits para representar conjuntos de señales**

`SigBlk`/`SigIgn`/`SigCgt`/`SigPnd`/`ShdPnd` son cada uno un número de 64
bits donde el **bit N-1** representa si la señal número **N** está presente
en ese conjunto (bit 0 = señal 1 = `SIGHUP`, bit 1 = señal 2 = `SIGINT`,
etc.). Es una forma compacta de representar "cuáles de las 64 señales
posibles aplican" sin necesitar una lista o array — con un solo entero de
64 bits alcanza, y comprobar membresía es una operación de CPU casi
instantánea.

**Cómo se comprueba si un bit específico está prendido**

1. Se arma una máscara con un único bit prendido en la posición deseada:
   `1 << (n - 1)` (desplazar el 1 hacia la izquierda `n-1` posiciones).
2. Se hace un AND a nivel de bits entre el número original y esa máscara:
   `numero & mascara`. El resultado es distinto de cero **únicamente** si
   ese bit puntual estaba prendido en el número original — todos los demás
   bits se anulan porque la máscara tiene ceros en el resto de las
   posiciones.

**Por qué usar el módulo `signal` en vez de un diccionario hecho a mano**

Python ya mantiene la relación completa número↔nombre de señal a través de
`signal.Signals(n).name`. Armar un diccionario propio duplicaría esa
información con riesgo de errores de tipeo, y sería menos portable entre
sistemas Unix (los números de señal pueden variar levemente entre
plataformas). Además, `signal` conoce nativamente el concepto de señales de
tiempo real (`SIGRTMIN`/`SIGRTMAX`) como un **rango**, sin necesidad de
enumerar cada una individualmente.

**Señales de tiempo real (33-64) sin nombre individual**

`signal.Signals(n)` lanza `ValueError` para números dentro del rango de
señales de tiempo real que no tienen una constante individual definida
(Python solo expone `SIGRTMIN`/`SIGRTMAX` como límites del rango, no
`SIGRT33`, `SIGRT34`, etc.). Se maneja con un `except ValueError` que
devuelve un nombre de fallback genérico (`f"SIG{n}"`), evitando que la
función explote y priorizando devolver algo útil aunque no sea el nombre
"oficial".

**Por qué esta vista no tiene un orden natural, a diferencia de las anteriores**

Resumen, Memoria, FDs y Threads tenían una métrica numérica única y
comparable entre procesos (CPU%, RSS, cantidad de FDs, cantidad de threads)
que justificaba un `sorted(...)` por esa métrica. Señales, en cambio, expone
**cinco listas distintas** sin una jerarquía obvia entre sí — ordenar por
"cantidad de señales ignoradas", por ejemplo, sería arbitrario y no
reflejaría nada especialmente relevante sobre el proceso. Se optó por un
orden estable y predecible (PID ascendente) en su lugar, explícito con
`.sort()`, ya que el orden que devuelve `listar_pids()` (basado en
`os.listdir`) no está garantizado por el sistema operativo.

**Contraste real entre `systemd` y `kthreadd` como ejemplo del concepto**

`systemd` (PID 1) ignora solo `SIGPIPE`, porque como proceso init necesita
manejar activamente casi todas las señales (reinicio de servicios,
propagación de shutdown, etc.) — la mayoría de sus señales relevantes están
en `SigCgt` (con handler propio), no en `SigIgn`. `kthreadd` (PID 2), en
cambio, ignora prácticamente el catálogo completo: al ser un kernel thread,
no tiene un espacio de manejo de señales de usuario como un proceso normal
— para el kernel, ese proceso "ignora todo" porque las señales de usuario
simplemente no le aplican de la misma manera.

### Conexión con la materia

- **Clase de señales**: catálogo estándar POSIX, diferencia entre señales
  bloqueadas/ignoradas/con handler propio/pendientes, y el caso especial de
  señales de tiempo real como extensión más moderna del mecanismo clásico.

### Código actual (agregado a `src/procfs.py`)

```python
import signal

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
```

**`src/analizadores/senales.py`**
```python
import time
from procfs import listar_pids, senales_de


def analizador_senales_loop(vista_compartida, intervalo=3):
    while True:
        filas = []
        for pid in listar_pids():
            datos = senales_de(pid)
            if datos is not None:
                filas.append(datos)

        filas.sort(key=lambda p: p["pid"])
        vista_compartida[:] = filas
        time.sleep(intervalo)
```

---

## Sesión 8 — Scheduling y Sistema: cierre de los 7 analizadores

### Qué hicimos

- Ampliamos `_leer_stat_desde_ruta` con los campos de scheduling: `sid`,
  `pgid` (campos 6-7), `priority` (18), `nice` (19), `rt_priority` (40),
  `policy` (41).
- Armamos `POLICIES` (diccionario manual de políticas del scheduler) y
  `nombre_policy()`, y `scheduling_de(pid)` combinando `leer_stat` +
  `leer_status` (`Cpus_allowed_list`, context switches).
- `analizadores/scheduling.py`: ordena por `nonvoluntary_ctxt_switches`
  (no por el total, ni por voluntarios), como métrica más reveladora de
  competencia real por CPU.
- Implementamos el último analizador, **Sistema** — el más distinto de
  todos: no itera por PID, lee archivos globales del kernel.
  - `leer_stat_global()`: primera línea de `/proc/stat` (jiffies acumulados
    globales por categoría: user, nice, system, idle, iowait).
  - `leer_loadavg()`, `leer_uptime()`, `leer_meminfo()`: parseo directo,
    reutilizando patrones ya conocidos (`partition(':')`, `_kb_a_int`).
  - `contar_procesos_por_estado()`: recorre todos los PIDs contando por
    estado, zombies, y threads totales.
  - `CalculadoraCPUGlobal` (en `cpu.py`): clase nueva y más simple que
    `CalculadoraCPU`, pensada para una única serie temporal (no un
    diccionario por PID), calculando % de user/system/idle/iowait a partir
    de dos lecturas de `/proc/stat`.
  - `sistema_global(calc_cpu_global)`: combina todo lo anterior en un solo
    diccionario. Recibe la calculadora como parámetro (no la crea
    internamente) para preservar el estado entre llamadas.
  - `analizadores/sistema.py`: único analizador que escribe a un
    `Manager().dict()` en vez de `Manager().list()`, porque la vista es un
    solo estado global, no una lista de filas por proceso.
- `main.py` final: **8 procesos corriendo en simultáneo** (Recolector + los
  7 analizadores), cada uno con su propio intervalo, todos comunicados vía
  `Manager`, sin errores. Con esto se completó la capa de datos completa
  del TP (las 7 vistas obligatorias de la consigna).

### Conceptos teóricos

**`/proc/stat`: jiffies globales por categoría**

La primera línea de `/proc/stat` (`cpu ...`) tiene el mismo principio que
`utime`/`stime` de un proceso individual: son contadores **acumulados** de
jiffies desde el arranque del sistema, agrupados por categoría (`user`,
`nice`, `system`, `idle`, `iowait`, y otras menos relevantes para el TP como
`irq`/`softirq`/`steal`). Igual que con CPU% por proceso, obtener un
porcentaje instantáneo requiere **delta entre dos lecturas**, no un solo
valor.

**Por qué el cálculo de CPU global no necesita dividir por `HZ`**

A diferencia de `CalculadoraCPU` (que sí divide por `HZ` para convertir
jiffies a segundos y así poder compararlos contra el tiempo real
transcurrido, `Δt`), el CPU global se calcula como una **proporción interna**:
`delta_categoría / delta_total_de_todas_las_categorías`. Como numerador y
denominador están en la misma unidad (jiffies), el factor `HZ` se cancela
matemáticamente al ser común a ambos — no aporta nada dividirlo.

**Por qué conviene una clase nueva (`CalculadoraCPUGlobal`) en vez de
reutilizar `CalculadoraCPU` con una clave inventada**

`CalculadoraCPU` está diseñada para trackear **múltiples series temporales
simultáneas** (una por PID), por eso usa un diccionario interno. El CPU
global es una única serie temporal — no hay "múltiples identificadores" que
trackear. Forzar el uso de `CalculadoraCPU` con una clave falsa (ej.
`"global"`) funcionaría, pero sería usar una estructura pensada para N series
cuando solo hace falta una, y oscurecería la intención del código. Una clase
separada, más simple (solo dos variables de instancia, sin diccionario), es
más clara y mejor ajustada al problema real.

**Por qué `/proc/stat`, `/proc/loadavg`, `/proc/meminfo`, `/proc/uptime` no
llevan `try/except`**

A diferencia de todos los archivos `/proc/<pid>/...`, que dependen de que un
proceso específico siga vivo (y por lo tanto pueden desaparecer en cualquier
momento), estos archivos son **globales del kernel** — existen mientras el
sistema esté corriendo y `/proc` esté montado, condición que ya es un
prerrequisito para que el monitor funcione en absoluto. Si llegaran a fallar,
sería síntoma de un problema mucho más grave que ameritaría que el programa
falle explícitamente, en vez de ocultar el error tratándolo como un caso
esperado (como sí se hace con procesos individuales que pueden morir en
cualquier momento).

**`load average`: promedios de 1/5/15 minutos**

Los tres valores de `/proc/loadavg` representan el promedio de procesos en
estado ejecutable (corriendo o esperando CPU) durante los últimos 1, 5 y 15
minutos respectivamente — dan una idea de tendencia de carga del sistema en
distintas escalas de tiempo, no solo un instante puntual como sí lo son los
demás datos de `/proc`.

**`/proc/uptime`: dos números con significados distintos**

El primer valor es el tiempo real transcurrido desde el arranque del
sistema (uptime en segundos). El segundo es la suma acumulada de tiempo
idle de **todos los cores combinados** — por eso en un sistema multi-core
puede ser mayor que el uptime real: si los N cores están ociosos
simultáneamente, ese tiempo se acumula multiplicado por N.

**Costo de `contar_procesos_por_estado`: dos lecturas por proceso, en cada vuelta**

La función llama tanto `leer_stat` como `leer_status` para cada uno de los
~300+ procesos del sistema en cada ciclo, solo para extraer `state` y
`Threads`. Es un costo conocido y aceptado para el alcance de este TP —no
crítico dado que Sistema no necesita ser la vista más urgente en
refrescarse—, pero vale la pena documentarlo como decisión consciente, no
como un descuido de performance.

**Por qué `sistema_global` recibe la calculadora como parámetro en vez de
crearla internamente**

Mismo principio que ya se aplicó con el diccionario de "lecturas previas"
del Recolector: un objeto que necesita **recordar estado entre llamadas**
(en este caso, `self._anterior` de `CalculadoraCPUGlobal`) tiene que
**vivir fuera** de la función que lo usa repetidamente, y pasarse como
parámetro. Si se creara una instancia nueva en cada llamada a
`sistema_global`, `self._anterior` sería siempre `None` al empezar, y el
cálculo de CPU% devolvería permanentemente `0.0` — nunca habría una lectura
anterior real con la cual comparar, porque el objeto se destruye al
terminar cada llamada.

**`Manager().dict()` vs. `Manager().list()` según la naturaleza de los datos**

Las vistas con "una fila por proceso" (Resumen, Memoria, FDs, Threads,
Señales, Scheduling) usan `Manager().list()`, actualizada con slicing
(`vista[:] = filas`). Sistema, en cambio, es un **único estado global** —
no hay "filas" que ordenar o iterar, sino un solo diccionario con
sub-estructuras (CPU%, loadavg, memoria, conteo de procesos). Por eso usa
`Manager().dict()`, actualizado con `.update(...)` — mismo patrón que ya se
usaba en el Recolector original, coherente con la elección de estructura
según la forma natural del dato, no por costumbre o uniformidad forzada.

### Conexión con la materia

- **Clase de scheduler**: políticas de scheduling de Linux
  (`SCHED_OTHER`/`FIFO`/`RR`/`BATCH`/`IDLE`/`DEADLINE`), y la diferencia
  entre context switches voluntarios (el proceso cede la CPU) e involuntarios
  (el scheduler expulsa al proceso) como señal de competencia real por CPU.
- **Clase de administración de memoria / stats del sistema**: cómo el
  kernel expone métricas agregadas de todo el sistema (no solo por proceso)
  a través de `/proc/stat`, `/proc/meminfo`, `/proc/loadavg`.

---

## Sesión 9 — Manejo de señales del propio monitor: patrón self-pipe

### Qué hicimos

- Implementamos `ManejadorSenales`, una clase que centraliza el manejo de
  `SIGINT`, `SIGTERM`, `SIGHUP`, `SIGUSR1`, `SIGUSR2` en `main.py` usando el
  **patrón self-pipe**: `signal.signal(...)` registra un handler mínimo que
  solo hace `os.write()` de un byte a un pipe interno; el loop principal usa
  `select.select(...)` con timeout para detectar cuándo hay algo para leer
  en ese pipe, y recién ahí procesa la señal con el trabajo real.
- Implementamos las 4 acciones concretas: `manejar_shutdown` (termina los 8
  procesos hijos con `terminate()` + `join()`), `manejar_dump` (vuelca el
  snapshot a un JSON con timestamp), toggle de `modo_verbose` (flag, sin
  conectar aún a los analizadores), y `manejar_reload_config` (intenta leer
  `config.json`, maneja `FileNotFoundError` prolijamente).
- Reemplazamos el `time.sleep(8)` fijo de `main.py` por un loop real e
  indefinido que espera señales con `esperar_senal(timeout=1.0)`.
- Probado en vivo desde una segunda terminal con `kill -USR1`, `kill -USR2`,
  `kill -HUP`, `kill -TERM` contra el PID real del monitor corriendo — las
  4 señales se comportaron como se esperaba, incluido el `SIGHUP` fallando
  ordenadamente por falta de `config.json` (pendiente de crear). El dump de
  `SIGUSR1` generó un JSON de ~5300 líneas con el snapshot completo.

### Conceptos teóricos

**Por qué un handler de señal no puede hacer "trabajo real" directamente**

Un handler de señal se ejecuta en un momento completamente impredecible —
puede interrumpir al programa principal en medio de cualquier operación,
incluso una que no terminó (ej. el Recolector a mitad de
`snapshot_compartido.update(...)`). Por eso solo están permitidas dentro de
un handler las funciones **async-signal-safe**: un conjunto reducido y
específico del sistema operativo (como `write()` a un FD ya abierto), que
excluye explícitamente cosas como `print()`, `open()`, `json.dump()`, o
crear/modificar estructuras de datos complejas de Python — todas ellas
pueden usar locks internos, alocar memoria, o ejecutar mucho código del
intérprete, y si la señal interrumpe otra operación que estaba usando esos
mismos recursos, el resultado puede ser un deadlock, datos corruptos, o
comportamiento indefinido.

**El patrón self-pipe: separar "avisar" de "actuar"**

Solución estándar al problema anterior: el handler de señal hace lo mínimo
posible —escribir un solo byte a un pipe ya abierto (`os.write`, que sí es
async-signal-safe)— y nada más. El **loop principal**, en un punto
controlado y seguro de su propia ejecución (fuera del contexto restringido
de un signal handler), detecta que llegó algo al pipe y recién ahí realiza
el trabajo real: abrir archivos, serializar a JSON, imprimir mensajes,
modificar estructuras compartidas. El handler "avisa"; el programa
principal "actúa".

**Por qué se escribe el número de señal (`signum`) y no un string**

Minimizar el trabajo dentro del handler es la prioridad — escribir un
número como un solo byte (`bytes([signum])`) es más simple y directo que
crear y codificar un string. Además, el número ya es exactamente lo que el
loop principal necesita para decidir qué acción tomar (comparándolo contra
las constantes de `signal`), sin necesidad de volver a interpretarlo.

**Por qué `select.select(...)` necesita un `timeout`, no bloqueo indefinido**

`main.py` no solo espera señales: también necesita, en teoría, seguir
controlando el estado del monitor (verificar que los procesos hijos sigan
vivos, actualizar la pantalla, etc.) incluso si no llega ninguna señal
durante un buen rato. Bloquear indefinidamente con `select.select([...], [],
[])` sin timeout dejaría el loop principal completamente inerte mientras no
llegue una señal. Con un `timeout` (ej. 1 segundo), el loop alterna
naturalmente entre "esperar señales" y "seguir con sus tareas normales",
manteniéndose reactivo a ambas cosas.

**`default=str` en `json.dump`**

`json` solo serializa nativamente tipos simples (`dict`, `list`, `str`,
`int`, `float`, `bool`, `None`). El snapshot del Recolector, una vez
convertido de proxy de `Manager` a `dict` normal (`dict(snapshot)`), en
principio solo contiene esos tipos — pero `default=str` actúa como red de
seguridad: si en algún momento se colara un tipo no serializable, en vez de
que `json.dump` explote con `TypeError`, lo convierte a string y sigue.

### Conexión con la materia

- **Clase 6 (Señales / self-pipe / signal.set_wakeup_fd)**: aplicación
  directa del patrón visto en clase para coordinar señales asincrónicas con
  un loop principal de forma segura.

### Código actual (`src/main.py`, fragmento relevante)

```python
class ManejadorSenales:
    def __init__(self):
        self._r, self._w = os.pipe()
        os.set_blocking(self._r, False)

        signal.signal(signal.SIGINT, self._handler)
        signal.signal(signal.SIGTERM, self._handler)
        signal.signal(signal.SIGHUP, self._handler)
        signal.signal(signal.SIGUSR1, self._handler)
        signal.signal(signal.SIGUSR2, self._handler)

    def _handler(self, signum, frame):
        os.write(self._w, bytes([signum]))

    def esperar_senal(self, timeout=1.0):
        listos, _, _ = select.select([self._r], [], [], timeout)
        if not listos:
            return None
        datos = os.read(self._r, 1)
        return datos[0]
```
---

## Sesión 10 — Config en caliente vía Manager, y el bug de herencia de señales en `fork`

### Qué hicimos

- Creamos `config.json` en la raíz del repo, con `intervalos` por vista y
  `filtros_default`.
- Reemplazamos el parámetro `intervalo` (fijo, congelado al crear cada
  `Process`) por un `config` compartido (`Manager().dict()`) en las 8
  funciones de loop (Recolector + 7 analizadores). Cada una ahora lee
  `config["intervalos"]["clave"]` **dentro** del `while True`, no una sola
  vez al arrancar — así un cambio de config se aplica sin reiniciar
  procesos.
- `manejar_reload_config` (disparado por `SIGHUP`) relee `config.json` y
  actualiza el `Manager().dict()` compartido con `.clear()` + `.update()`.
  Probado en vivo: se cambió `config.json` con el monitor corriendo, se
  mandó `SIGHUP`, y el nuevo intervalo se reflejó en el `config` compartido
  sin reiniciar nada.
- **Bug real encontrado y corregido:** al intentar cerrar el monitor con
  `Ctrl+C`/`kill -TERM`, los procesos hijos no terminaban — `p.join()`
  quedaba esperando indefinidamente, y solo `kill -9` (SIGKILL, no
  interceptable) lograba matarlos.
- Diagnóstico: los procesos hijos, creados con `fork` **después** de que el
  proceso principal ya había registrado sus handlers personalizados de
  señal (`ManejadorSenales`), **heredaron esos mismos handlers**. Cuando
  `p.terminate()` mandaba `SIGTERM` a un hijo, este no terminaba (el
  comportamiento default de `SIGTERM`) sino que ejecutaba el handler
  heredado —que solo escribe un byte al pipe y sigue corriendo su loop—.
- Fix: `restaurar_senales_hijo()` (nuevo módulo `procesos.py`), llamada al
  principio de cada función de loop de proceso hijo, que restaura
  `SIGTERM` a su comportamiento default (`SIG_DFL`, sí termina el proceso)
  e ignora explícitamente (`SIG_IGN`) el resto de las señales que solo
  tienen sentido a nivel del proceso principal (`SIGINT`, `SIGHUP`,
  `SIGUSR1`, `SIGUSR2`).

### Conceptos teóricos

**Por qué el intervalo fijo como argumento no permite reload en caliente**

Un parámetro normal de función (`def recolector_loop(snapshot, intervalo)`)
se evalúa **una sola vez**, en el momento en que se llama la función —y en
el caso de `multiprocessing.Process(target=..., args=(..., intervalo))`,
ese valor queda fijo para toda la vida del proceso, sin forma de cambiarlo
desde afuera una vez que arrancó. La solución: reemplazar ese valor
"congelado" por un **objeto compartido** (`Manager().dict()`) que el
proceso relee en cada vuelta de su loop — la misma herramienta que ya se
usaba para todo lo demás en el TP (snapshot, vistas), aplicada ahora a la
configuración misma.

**Por qué actualizar el config con `.clear()` + `.update()` en vez de
reasignar la variable**

Mismo principio ya aplicado desde el Recolector original: reasignar
`config_compartido = nueva_config` rompería la conexión con el proxy real
del `Manager` (la variable local pasaría a apuntar a un dict Python normal
nuevo). Mutar el objeto existente (`.clear()` seguido de `.update()`)
preserva la conexión, y todos los procesos que ya tienen una referencia al
mismo proxy ven el cambio inmediatamente.

**Herencia de handlers de señal en procesos creados con `fork`**

Un proceso hijo creado con `fork()` es, en el momento de la creación, una
copia completa del estado del padre — **incluyendo los handlers de señal
que el padre ya tenía registrados** en ese instante. Si el proceso
principal registra handlers personalizados (como el self-pipe de
`ManejadorSenales`) **antes** de lanzar a los hijos, estos heredan
exactamente esos mismos handlers, no el comportamiento default del sistema
operativo para esas señales.

**Por qué esto rompe `Process.terminate()`**

`terminate()` funciona internamente enviando `SIGTERM` al proceso hijo,
confiando en que el comportamiento default de esa señal (terminar el
proceso) se ejecute. Pero si el hijo heredó un handler personalizado para
`SIGTERM` que solo hace `os.write()` a un pipe y continúa su ejecución
normal (como el handler del self-pipe, diseñado para el *proceso
principal*, no para los hijos), el hijo **nunca termina** ante esa señal —
sigue corriendo su `while True` indefinidamente. `p.join()`, que espera a
que el proceso realmente termine, se queda bloqueado para siempre. Solo
`SIGKILL` (`kill -9`) funciona en ese estado, porque es la única señal que
**no puede ser interceptada, bloqueada ni ignorada** por ningún handler —
está fuera del alcance de cualquier lógica de aplicación, a propósito,
justamente para servir como último recurso del sistema operativo.

**La solución: restaurar el comportamiento de señales al inicio de cada
proceso hijo**

Cada función de loop de un proceso hijo llama, como primera línea,
`restaurar_senales_hijo()`, que usa `signal.signal(...)` para:
- `SIGTERM` → `signal.SIG_DFL` (comportamiento default: si el proceso
  recibe esta señal, termina — necesario para que `p.terminate()` funcione).
- `SIGINT`, `SIGHUP`, `SIGUSR1`, `SIGUSR2` → `signal.SIG_IGN` (ignorar
  explícitamente): estas señales solo tienen sentido de ser manejadas por
  el proceso principal (shutdown coordinado, reload de config, dump,
  toggle verbose); si un hijo las heredara sin restaurar, podría reaccionar
  de forma no deseada ante una señal que en realidad iba dirigida al
  comportamiento del monitor completo, no a ese proceso individual.

**Por qué el módulo se llamó `procesos.py` y no `senales.py`**

Ya existía `analizadores/senales.py` (el analizador de la vista de señales
por proceso, que decodifica `SigBlk`/`SigIgn`/etc.). Crear otro archivo
`senales.py` en `src/` habría generado dos módulos con el mismo nombre en
paquetes distintos — técnicamente válido en Python (paquetes distintos
evitan colisión real), pero confuso de leer y propenso a errores de import
por descuido (`from senales import x` vs `from analizadores.senales import
x`). `procesos.py` refleja mejor su responsabilidad real: gestión del
ciclo de vida y señales de los procesos del monitor, distinta tanto de
`procfs.py` (lectura de `/proc`) como de `analizadores/senales.py`
(decodificación de señales de *otros* procesos del sistema, no del propio
monitor).

### Conexión con la materia

- **Clase de fork/exec y herencia de estado**: qué copia exactamente un
  `fork()` del proceso padre (incluidos handlers de señal ya registrados),
  y cómo eso puede generar comportamiento no intencional si no se restaura
  explícitamente en el hijo.
- **Clase de señales**: `SIGKILL` como señal no interceptable por diseño,
  y la diferencia entre comportamiento default (`SIG_DFL`), ignorado
  (`SIG_IGN`), y manejado por handler personalizado.

### Código actual

**`config.json`** (raíz del repo)
```json
{
  "intervalos": {
    "recolector": 1, "resumen": 2, "memoria": 3, "fds": 5,
    "threads": 2, "senales": 10, "scheduling": 3, "sistema": 2
  },
  "filtros_default": { "usuario": null, "estado_minimo_interes": null }
}
```

**`src/procesos.py`**
```python
import signal


def restaurar_senales_hijo():
    """
    Los procesos hijos creados con fork() heredan los handlers de señal
    que el proceso padre tenía registrados en ese momento -- incluido el
    handler personalizado de ManejadorSenales (self-pipe), que NO termina
    el proceso ante SIGTERM, solo escribe al pipe y sigue.

    Se llama al principio de cada función de loop de un proceso hijo,
    antes de entrar al while True.
    """
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    signal.signal(signal.SIGUSR2, signal.SIG_IGN)
```

**Patrón aplicado en las 8 funciones de loop** (ejemplo con `recolector_loop`):
```python
from procesos import restaurar_senales_hijo

def recolector_loop(snapshot_compartido, config):
    restaurar_senales_hijo()
    calc_cpu = CalculadoraCPU()
    while True:
        # ...
        intervalo = config["intervalos"].get("recolector", 1)
        time.sleep(intervalo)
```

---

## Sesión 11 — `display.py`: interfaz TUI con rich, migración a `Value`, y el bug de `/dev/tty`

### Qué hicimos

- **Migración de intervalos: `Manager().dict()` → `multiprocessing.Value`.**
  La consigna pide explícitamente `Value` para la comunicación
  display↔analizador de intervalos (no `Manager`). Se reemplazó el
  `config` compartido único por un diccionario de 8 `Value("d", ...)`
  individuales (uno por vista + Recolector), agrupados en un dict Python
  normal (`intervalos = {"resumen": Value(...), ...}`) para pasarlos
  prolijamente a cada proceso. `manejar_reload_config` (disparado por
  `SIGHUP`) ahora actualiza cada `Value` individualmente con `.value = ...`
  en vez de `.update()` sobre un Manager.
- Aplicado en los 8 archivos de loop (Recolector + 7 analizadores):
  parámetro `config` → `intervalo`, y `config["intervalos"].get(...)` →
  `intervalo.value`, leído en cada vuelta del `while True`.
- Implementamos `display.py` completo con `rich`: `Live` para refresco de
  pantalla, tablas por vista (`armar_tabla_resumen`, `_memoria`, `_fds`,
  etc.), navegación con teclas `1`-`7`, ajuste de intervalo con `+`/`-`
  usando `intervalos[vista].get_lock()` para atomicidad del `+=`, y salida
  con `q`.
- Lectura de teclado en un **thread** (no `multiprocessing.Process`)
  dentro del propio proceso de display, usando el patrón
  productor-consumidor con `queue.Queue` (versión para threads, distinta
  de `multiprocessing.Queue`).
- **Bug real encontrado y corregido:** el lector de teclado fallaba con
  `termios.error: (25, 'Inappropriate ioctl for device')` al correr dentro
  de un proceso hijo de `multiprocessing`, aunque el mismo código
  funcionaba perfecto en un thread del proceso principal.
- Diagnóstico paso a paso: se probó el mecanismo aislado primero en un
  thread simple (funcionó), después dentro de un `multiprocessing.Process`
  (falló), y se agregaron prints de diagnóstico (`sys.stdin.isatty()` en
  padre vs. hijo) que confirmaron la causa: **`multiprocessing` cierra
  `sys.stdin` en cada proceso hijo y lo reemplaza por `/dev/null`**,
  específicamente para evitar que múltiples procesos lean de la misma
  terminal de forma impredecible.
- Fix: reemplazar `sys.stdin` por `/dev/tty` (abierto directamente con
  `os.open('/dev/tty', os.O_RDONLY)`), que en Linux siempre representa la
  terminal controladora real del proceso, sin pasar por `sys.stdin` para
  nada. Con ese cambio, el teclado funcionó correctamente dentro del
  proceso de display.
- Sistema probado de punta a punta: `main.py` corriendo con los 9 procesos
  (Recolector + 7 analizadores + display), navegación entre vistas,
  ajuste de intervalo, y salida, todo funcionando.

### Conceptos teóricos

**Por qué la consigna pide `Value` específicamente para intervalos, y no `Manager`**

`Manager` está pensado para compartir **estructuras complejas** (dicts,
lists) entre procesos, a costa de levantar un proceso servidor propio que
gestiona proxies — overhead razonable para datos como el snapshot completo
de procesos, pero innecesario para un único número que además se consulta
y modifica con mucha frecuencia (el display puede tocarlo varias veces por
segundo con `+`/`-`). `Value` es memoria compartida directa (`mmap`), sin
proceso intermediario, apropiada específicamente para un dato escalar de
acceso frecuente. La cátedra pide `Value` acá para verificar que se sepa
distinguir cuándo corresponde cada primitiva, no solo que el programa
funcione con cualquiera de las dos.

**`Value(lock=True)` por defecto, y cuándo usar `get_lock()` explícito**

`multiprocessing.Value` incluye un lock interno por defecto
(`lock=True`), que protege lecturas y escrituras individuales de `.value`
contra condiciones de carrera entre procesos. Sin embargo, una operación
compuesta como `intervalo.value += 0.5` es en realidad **dos pasos**
(leer, luego escribir) — para garantizar que esa operación completa sea
atómica frente a otro proceso escribiendo al mismo tiempo (ej. un reload
por `SIGHUP` en simultáneo con un `+` del usuario), hace falta envolver la
operación compuesta explícitamente con `with value.get_lock():`.

**Por qué el lector de teclado usa un thread y no un proceso**

El trabajo del lector de teclado es I/O-bound (bloqueado esperando que el
usuario apriete una tecla la mayor parte del tiempo) — el GIL no
representa un problema real acá, porque se libera durante la espera
bloqueante de I/O y no compite por CPU con el resto del programa. Además,
lo único que hay que compartir entre el lector y el loop principal de
display es un evento muy simple (una tecla), no una estructura compleja —
usar un `multiprocessing.Process` aparte requeriría un mecanismo de IPC
completo (`Queue`/`Pipe` de multiprocessing) solo para pasar caracteres
sueltos, complejidad innecesaria frente a un thread que comparte memoria
naturalmente con el proceso de display.

**`tty.setcbreak` y por qué hace falta restaurar la terminal al salir**

Por defecto la terminal opera en modo canónico (buffer de línea completa,
no entrega nada hasta Enter). `tty.setcbreak(fd)` cambia el modo a
carácter-por-carácter, necesario para que las teclas de navegación
respondan al instante. Es crítico restaurar la configuración original de
la terminal (`termios.tcsetattr(fd, termios.TCSADRAIN, config_original)`)
en un `finally`, ya que si el programa termina (normal o
anormalmente) sin restaurar, la terminal del usuario queda en un estado
roto (sin eco de texto, sin saltos de línea normales) incluso después de
cerrar el programa.

**`multiprocessing` cierra `sys.stdin` en los procesos hijos**

Comportamiento interno de la librería estándar de Python (no algo
derivable solo con razonamiento sobre `fork`): al arrancar un proceso hijo,
`multiprocessing` cierra explícitamente `sys.stdin` y lo reemplaza por
`/dev/null`, para evitar que múltiples procesos lean de la misma terminal
de forma impredecible y se "roben" caracteres entre sí de manera no
determinística. Esto significa que, aunque `fork()` en sí copia todos los
file descriptors del padre (incluyendo el 0, stdin), Python específicamente
deshace esa herencia para `stdin` apenas arranca el bootstrap del proceso
hijo.

**`/dev/tty` como vía alternativa a `sys.stdin` para acceder a la terminal**

En Linux, `/dev/tty` es un dispositivo especial que siempre referencia la
terminal controladora del proceso que lo abre, independientemente de cómo
esté configurado `sys.stdin` en ese momento. Abrirlo directamente
(`os.open('/dev/tty', os.O_RDONLY)`) evita por completo el problema de que
`multiprocessing` haya cerrado `sys.stdin`, porque no depende de ese file
descriptor específico — accede al mismo dispositivo de terminal por una
vía distinta, a nivel de sistema de archivos.

**Metodología de diagnóstico aplicada: aislar la variable, no adivinar**

Frente al bug, se probó primero el mecanismo más simple posible (thread
dentro del proceso principal, sin `multiprocessing.Process` de por medio)
para confirmar que la lógica de lectura de teclado en sí era correcta.
Recién después se probó exactamente el mismo código dentro de un
`multiprocessing.Process`, aislando la variable que cambiaba entre el caso
que funcionaba y el que fallaba. Con eso confirmado, se agregaron prints
de diagnóstico específicos (`isatty()` en padre vs. hijo) para verificar
la hipótesis concreta antes de aplicar cualquier fix — evitando "probar
soluciones a ciegas" sin entender la causa raíz.

### Conexión con la materia

- **Clase de multiprocessing**: diferencia de uso entre `Value` y
  `Manager` según la naturaleza y frecuencia de acceso del dato compartido.
- **Clase de threads**: cuándo un thread es la herramienta apropiada
  dentro de una arquitectura multiproceso (trabajo I/O-bound, estado
  simple a compartir) vs. cuándo hace falta un proceso completo.
- **Clase de terminales / dispositivos especiales de Linux**: `/dev/tty`
  como vía de acceso directo a la terminal controladora, independiente de
  la configuración de `stdin` del proceso.

### Código actual (fragmentos relevantes)

**Migración de intervalos en `main.py`:**
```python
intervalos = {
    "recolector": mp.Value("d", config_inicial["intervalos"]["recolector"]),
    "resumen": mp.Value("d", config_inicial["intervalos"]["resumen"]),
    # ... una entrada por vista
}

def manejar_reload_config(intervalos):
    nueva_config = cargar_config_desde_archivo()
    nuevos = nueva_config.get("intervalos", {})
    for nombre, value in intervalos.items():
        if nombre in nuevos:
            value.value = float(nuevos[nombre])
```

**Patrón aplicado en cada loop (ejemplo `resumen.py`):**
```python
def analizador_resumen_loop(snapshot_recolector, vista_compartida, intervalo):
    restaurar_senales_hijo()
    while True:
        # ...
        time.sleep(intervalo.value)
```

**`display.py` — lector de teclado con el fix de `/dev/tty`:**
```python
def leer_teclado_loop(cola_teclas, detener_evento):
    tty_fd = os.open('/dev/tty', os.O_RDONLY)
    config_original = termios.tcgetattr(tty_fd)
    try:
        tty.setcbreak(tty_fd)
        while not detener_evento.is_set():
            tecla = os.read(tty_fd, 1).decode(errors='replace')
            cola_teclas.put(tecla)
    finally:
        termios.tcsetattr(tty_fd, termios.TCSADRAIN, config_original)
        os.close(tty_fd)
```

**Ajuste de intervalo con lock explícito (dentro de `display_loop`):**
```python
elif tecla == "+":
    with intervalos[vista_actual].get_lock():
        intervalos[vista_actual].value += 0.5
```
---

## Sesión 12 — Docker, y coordinación de salida entre `main.py` y `display.py`

### Qué hicimos

- Armamos `Dockerfile` (base `python:3.12-slim`, orden de capas optimizado
  para cache: `requirements.txt` + `pip install` antes de copiar el resto
  del código) y `docker-compose.yml` (`tty: true` + `stdin_open: true`
  para que `/dev/tty` exista dentro del contenedor; bind mount de
  `config.json` para permitir reload en caliente sin reconstruir la
  imagen).
- Probado en vivo: `docker compose up --build` levantó el contenedor con
  la TUI funcionando, y `docker kill --signal=SIGUSR1 monitor-procesos`
  confirmó que las señales llegan correctamente al proceso dentro del
  contenedor.
- Se observó que, corriendo en Docker, el monitor solo ve **sus propios 9
  procesos** (namespace de PIDs aislado del contenedor) — comportamiento
  esperado del aislamiento de Docker, no un bug.
- **Bug de UX encontrado y corregido:** al mandar `SIGUSR1` con el monitor
  corriendo, el mensaje de confirmación (`print()` en `manejar_dump`)
  aparecía insertado en medio de la pantalla que `rich.Live` estaba
  controlando, rompiendo el layout — porque dos procesos distintos
  (`main.py` y `display.py`) escribían a la misma terminal sin ninguna
  coordinación entre sí.
- Solución diseñada y aplicada: `main.py` dejó de hacer `print()` directo
  para estos eventos. En su lugar, usa una `multiprocessing.Queue`
  (`cola_eventos`) para comunicarle el mensaje a `display.py`, que lo
  muestra dentro de su propio layout controlado por `rich`. Además, se
  agregó `logger_monitor.py` con `registrar_evento(...)`, que escribe cada
  evento con timestamp a `monitor.log` de forma persistente, independiente
  de si el usuario llegó a verlo en pantalla a tiempo.
- Validado en vivo: el mensaje de dump apareció correctamente dentro del
  pie de la interfaz (en verde), sin romper el layout, y `monitor.log`
  registró ambos eventos (arranque del monitor y dump) con timestamp.

### Conceptos teóricos

**Orden de capas en el `Dockerfile` para aprovechar el cache de build**

Docker cachea cada instrucción del `Dockerfile` como una capa; si una capa
no cambió respecto al build anterior, la reutiliza en vez de rehacerla.
Copiar primero `requirements.txt` e instalar dependencias, **antes** de
copiar el resto del código fuente, significa que mientras no cambien las
dependencias, `pip install` no se vuelve a ejecutar en cada build —solo
cuando cambia algo del propio código Python, que se copia después—,
acelerando considerablemente los rebuilds durante el desarrollo.

**`tty: true` + `stdin_open: true`: por qué son obligatorias para este proyecto puntual**

`display.py` abre `/dev/tty` directamente para leer el teclado (fix de la
sesión anterior). Ese dispositivo solo existe y funciona si el contenedor
tiene una pseudo-terminal interactiva asignada — exactamente lo que logran
esas dos flags en `docker-compose.yml`. Sin ellas, el mismo error que se
diagnosticó y corrigió en la sesión de `display.py` volvería a aparecer,
aunque el código en sí esté bien: el problema no sería de lógica, sino de
que Docker no le dio al contenedor una terminal real para abrir.

**Bind mount para reload en caliente dentro de Docker**

`COPY config.json .` en el `Dockerfile` copia el archivo **una sola vez**,
al momento de construir la imagen — ediciones posteriores al archivo en la
máquina host no se reflejan dentro del contenedor ya corriendo. Un
**bind mount** (`volumes: - ./config.json:/app/config.json` en
`docker-compose.yml`) hace que el archivo dentro del contenedor sea
literalmente el mismo archivo del host, no una copia — así, editar
`config.json` en la máquina real y mandar `SIGHUP` al proceso del
contenedor sigue funcionando exactamente igual que corriendo el monitor
sin Docker.

**Namespace de procesos aislado dentro de un contenedor**

`/proc` dentro de un contenedor Docker refleja el namespace de PIDs
*del contenedor*, no el del sistema host — por eso el monitor, corriendo
en Docker, solo ve sus propios procesos (Recolector + 7 analizadores +
display, todos hijos de `main.py`), y el propio `main.py` termina siendo
el PID 1 (equivalente al proceso init) dentro de ese namespace aislado.
Es el comportamiento esperado y correcto del aislamiento que ofrece
Docker, no una limitación del monitor en sí.

**Por qué dos procesos escribiendo a la misma terminal sin coordinación rompe la pantalla**

`rich.Live(screen=True)` toma control completo de la pantalla, gestionando
su propio buffer de refresco. Si otro proceso (`main.py`) hace un
`print()` normal mientras tanto, ese texto se inserta directamente en el
stream de salida de la terminal sin que `rich` sepa que eso ocurrió —
generando una pantalla rota o con contenido superpuesto de forma
impredecible. La solución no es "imprimir con más cuidado", sino que
**un único proceso controle la pantalla en cada momento**: el resto de los
procesos comunican lo que necesitan mostrar a través de un canal de datos
(`multiprocessing.Queue`), y es ese único proceso (`display.py`) el que
decide cómo y cuándo integrarlo a su propio layout.

**`multiprocessing.Queue` vs. `queue.Queue`: mismo patrón productor-consumidor, alcance distinto**

Ambas resuelven el mismo problema general (pasar datos de un productor a
un consumidor de forma segura), pero a escalas distintas. `queue.Queue`
solo funciona dentro de un mismo proceso, compartiendo memoria entre
threads (como `cola_teclas`, compartida entre el thread lector de teclado
y el loop principal de `display.py`, ambos en el mismo proceso).
`multiprocessing.Queue` cruza la frontera entre **procesos distintos**
(como `cola_eventos`, entre `main.py` y `display.py`), implementando IPC
real por debajo (pipes + serialización) — necesario porque procesos
distintos no comparten memoria, y una `queue.Queue` normal simplemente no
sería visible desde otro proceso.

### Conexión con la materia

- **Clase de contenedores / namespaces de Linux**: cómo Docker aísla el
  namespace de procesos (PID namespace), y por qué `/proc` dentro de un
  contenedor solo expone los procesos de ese namespace.
- **Clase de IPC**: distinción de alcance entre mecanismos de
  sincronización/comunicación para threads (memoria compartida directa,
  sin serialización) vs. para procesos (necesitan un canal de comunicación
  real, con algo de overhead de serialización).

### Código actual (fragmentos relevantes)

**`Dockerfile`**
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY config.json .
COPY src/ ./src/
WORKDIR /app/src
CMD ["python3", "main.py"]
```

**`docker-compose.yml`**
```yaml
services:
  monitor:
    build: .
    container_name: monitor-procesos
    tty: true
    stdin_open: true
    volumes:
      - ./config.json:/app/config.json
```

**`logger_monitor.py`**
```python
def registrar_evento(mensaje):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(RUTA_LOG, "a") as f:
        f.write(f"[{timestamp}] {mensaje}\n")
```

**Patrón de comunicación de eventos (`main.py` → `display.py`):**
```python
# main.py
cola_eventos = mp.Queue()
# ...
def manejar_dump(snapshot, cola_eventos):
    # ... genera el dump ...
    mensaje = f"Snapshot volcado a {nombre_archivo}"
    registrar_evento(mensaje)
    cola_eventos.put(mensaje)

# display.py, dentro del loop principal:
while not cola_eventos.empty():
    ultimo_evento = cola_eventos.get_nowait()
```

---

## Sesión 13 — Alineando `display.py` con los keybindings obligatorios de la consigna

### Qué hicimos

- Al releer la consigna con más cuidado, se encontró una discrepancia
  importante: la tabla de atajos de teclado obligatoria difería bastante
  de lo implementado hasta la sesión anterior. Faltaban: letras
  mnemotécnicas por vista (`r/m/f/t/s/p/g`, además de `1`-`7`), navegación
  con flechas (cursor de selección), pin con `Enter` sobre el proceso
  **seleccionado** (no automáticamente el primero de la lista), filtros de
  texto por comando (`/`) y usuario (`u`), toggle de criterio de orden
  (`c`), y pantalla de ayuda (`h`/`?`). También los intervalos default y
  mínimos por vista no coincidían exactamente con la tabla de la consigna
  (Scheduling, en particular: la consigna pide default 10s/mínimo 5s, se
  tenía 3s sin mínimo).
- Se detectó además un **choque de teclas real**: `p` ya se usaba para
  "pin" en la versión anterior, pero la consigna reserva `p` como letra
  mnemotécnica de la vista **Scheduling**.
- `config.json` corregido con los intervalos y mínimos exactos de la tabla
  de la consigna, agregando una nueva clave `intervalos_minimos`.
- Reescritura completa de `display.py`:
  - **Estado agrupado en un diccionario** (`estado = {...}`) en vez de
    parámetros sueltos, dado el número de piezas de estado mutable que se
    sumaron (cursor, filtros activos, modo de entrada de texto, criterio
    de orden, pin, etc.) — mismo criterio de "agrupar cuando hay
    demasiadas piezas relacionadas" ya aplicado antes en el TP (por
    ejemplo, con el diccionario de `Value` de intervalos).
  - **Detección de flechas** como secuencias de escape (`\x1b[A`/`\x1b[B`)
    en `leer_teclado_loop`, traducidas a los strings `"ARRIBA"`/`"ABAJO"`
    antes de encolarlas.
  - **Modo de entrada de texto** para los filtros `/` (comando) y `u`
    (usuario): al apretar la tecla, el display entra en `modo_entrada` y
    acumula caracteres en un buffer hasta `Enter` (aplica el filtro) o
    `Esc` (cancela) -- resolviendo con el mecanismo de teclado
    carácter-por-carácter ya existente, sin necesitar nada nuevo a bajo
    nivel.
  - `Enter` pinea/despinea el proceso bajo el **cursor de selección**
    (`indice_cursor`), no automáticamente el primero de la lista.
  - `c` cicla el criterio de orden (`cpu` → `rss` → `pid` → `cpu`...),
    aplicado de forma completa solo en la vista Resumen (única con esos
    tres campos comparables entre sí).
  - `+`/`-` ahora respetan el **mínimo por vista** (`INTERVALOS_MINIMOS`),
    en vez de un piso global fijo de 0.5s.
  - `h`/`?` muestra un panel de ayuda con el listado completo de atajos;
    cualquier tecla lo cierra.
- Probado en vivo: todos los atajos funcionando (cambio de vista por
  número y letra, navegación con flechas, pin con Enter, filtros de texto,
  toggle de orden, ayuda).

### Conceptos teóricos

**Por qué agrupar el estado mutable en un diccionario en vez de parámetros sueltos**

Cuando el número de piezas de estado que varias funciones necesitan leer
y/o modificar crece más allá de 3-4 (acá: vista actual, cursor, pin,
filtro de usuario, filtro de comando, modo de entrada, buffer de entrada,
orden, último evento, flag de ayuda), pasarlas como parámetros individuales
a cada función se vuelve difícil de mantener y propenso a errores (olvidar
pasar uno, invertir el orden). Agruparlas en un único diccionario (o un
objeto, en un diseño más orientado a objetos) que se pasa entero entre
funciones es más prolijo y escala mejor a medida que se agregan nuevas
piezas de estado.

**Captura de secuencias de escape para detectar teclas especiales**

Las flechas del teclado no generan un único carácter — en una terminal
en modo raw/cbreak, apretar la flecha arriba envía la secuencia de 3 bytes
`\x1b` (ESC) + `[` + `A` (flecha abajo: `\x1b[B`). El lector de teclado
detecta el primer byte especial (`\x1b`) y, al encontrarlo, lee
explícitamente los 2 bytes siguientes para distinguir qué secuencia
completa llegó, traduciéndola a un evento con nombre legible (`"ARRIBA"`,
`"ABAJO"`) antes de encolarla — así el resto del código nunca tiene que
lidiar con bytes de escape crudos.

**Entrada de texto con un lector de teclado carácter-por-carácter**

No hizo falta ningún mecanismo nuevo para soportar "escribir un filtro de
texto": alcanzó con introducir un estado (`modo_entrada`) que cambia cómo
se interpretan las teclas siguientes. Mientras `modo_entrada` está activo,
cada carácter imprimible se agrega a un buffer en vez de disparar su
acción normal (cambiar de vista, pinear, etc.), hasta que llega `Enter`
(confirma) o `Esc` (cancela). Es el mismo patrón que se había descartado
como "más complejo" en el diseño original del pin (opción B, sesión 12),
pero que terminó siendo necesario igual para los filtros de texto — la
complejidad extra fue proporcional al beneficio real que aportaba acá.

**Por qué el toggle de orden (`c`) solo tiene efecto pleno en una vista**

El campo por el que se puede ordenar de forma significativa depende de
qué datos tiene cada vista: Resumen tiene tanto `cpu_pct` como `vmrss_kb`
y `pid`, los tres campos que pide alternar la consigna. Otras vistas (FDs,
Threads, Señales, Scheduling) ya tienen su propio criterio de orden fijo
y significativo para esa vista específica (cantidad de FDs, de threads,
etc.), y no necesariamente tienen los mismos tres campos para alternar
entre ellos de la misma manera.

### Conexión con la materia

- Ninguna nueva particularmente — esta sesión fue más de ingeniería de
  interfaz y ajuste a especificación que de conceptos nuevos del sistema
  operativo; los conceptos de fondo (threads, IPC, memoria compartida) ya
  estaban cubiertos en sesiones anteriores.

---

## Sesión 14 — `docker compose up` vs `docker compose run`: teclado no funcionaba en Docker

### Qué hicimos

- Al levantar el contenedor con `docker compose up --build`, la TUI se
  veía correctamente (tabla, pie con atajos), pero **el teclado no
  respondía**: ni cambio de vista, ni pin, ni nada. Con `tty: true` y
  `stdin_open: true` ya seteados en `docker-compose.yml`, en teoría el
  problema de `/dev/tty` de la sesión 11 ya estaba resuelto.
- Diagnóstico: `docker compose up` multiplexa la salida con el prefijo del
  nombre del servicio (`monitor-procesos |`, visible en las capturas de
  pantalla) — comportamiento pensado para *logs* de servicios corriendo en
  background, no para una sesión interactiva directa. El teclado no se
  enruta de forma confiable al `/dev/tty` del contenedor en ese modo,
  aunque las flags de `tty`/`stdin_open` estén correctamente configuradas.
- Fix: usar `docker compose run --rm monitor` en vez de `docker compose up
  --build`. `run` da una sesión de terminal directa al contenedor, sin el
  multiplexado de logs, y con eso el teclado empezó a responder
  correctamente.

### Conceptos teóricos

**`docker compose up` vs. `docker compose run`**

`up` está diseñado para levantar uno o más servicios de forma similar a
como correrían en producción: outputs multiplexados con prefijo de
servicio, pensado para poder correr varios contenedores a la vez y
distinguir de dónde viene cada línea de log. `run` está pensado
específicamente para ejecutar **un** servicio de forma puntual e
interactiva, dando una sesión de terminal directa sin intermediarios —
la opción correcta quando la aplicación necesita realmente leer del
teclado en tiempo real, como una TUI.

---

## Sesión 15 — Auditoría de campos por vista: completando lo que faltaba mostrar

### Qué hicimos

- Se comparó, vista por vista, lo que pedía la consigna contra lo que
  realmente mostraba `display.py`. Los datos ya estaban todos calculados
  en `procfs.py` desde sesiones anteriores, pero varias tablas solo
  mostraban un subconjunto:
  - **Resumen**: faltaban `PPID` y `Threads` como columnas.
  - **Memoria**: solo se mostraba `heap` de los 5 segmentos, y faltaban
    6 de las 8 métricas de `status` (`VmSize`, `VmData`, `VmStk`, `VmExe`,
    `VmLib`, `VmHWM`, `VmSwap`).
  - **FDs y Threads**: la tabla solo mostraba un conteo agregado por
    proceso -- el detalle individual (destino/tipo de cada FD, o
    nombre/estado/context switches de cada thread) no se veía en
    ningún lado, aunque los datos ya estaban disponibles en cada fila.
  - **Señales**: solo 2 de las 5 categorías requeridas (`Ignoradas`,
    `Con handler`) se mostraban; faltaban `Bloqueadas`, `Pendientes
    proceso`, `Pendientes grupo`.
  - **Scheduling**: faltaban `SID`, `PGID`, `Priority`, `RT Priority`,
    `CPU Affinity`, y los context switches voluntarios.
  - **Sistema**: faltaban `Uptime` y el desglose de `Swap`.
- Se completaron todas las tablas con los campos faltantes.
- Para FDs y Threads específicamente, se agregó un **panel de detalle**
  separado (debajo de la tabla principal), que muestra la lista completa
  de FDs o threads del proceso bajo el cursor de selección -- ya que esos
  datos son una *lista* por proceso, no un escalar, y no entran como una
  columna más de la tabla principal.
- Se agregó el campo `comm` a las filas de `analizadores/fds.py` y
  `analizadores/threads.py` (antes solo tenían `pid` y el conteo), usando
  `leer_stat(pid)` para poder mostrar el nombre del proceso también en
  esas vistas.

### Conceptos teóricos

**Por qué una lista por proceso no puede ser "una columna más"**

Una tabla de "un proceso por fila" funciona bien cuando cada dato es un
escalar (un número, un string) por proceso. FDs y threads son
estructuralmente distintos: cada proceso tiene una **cantidad variable**
de FDs o threads, cada uno con sus propios sub-campos. Forzar eso en una
columna de la tabla principal implicaría concatenar todo en un string
ilegible. La solución más clara es una tabla secundaria de detalle,
enfocada en un solo proceso a la vez (el que está bajo el cursor de
navegación) -- reutilizando el mismo mecanismo de cursor que ya se había
armado para el pin, aplicado ahora también para elegir "de qué proceso
quiero ver el desglose".

**Auditar la implementación contra la especificación, no solo "que funcione"**

El código corría sin errores y mostraba datos reales en las 7 vistas --
"funcionaba" en el sentido de no crashear. Pero funcionar no es lo mismo
que cumplir la especificación completa: varias vistas mostraban un
subconjunto de lo pedido sin que hubiera ningún error visible que lo
señalara. Vale la pena, antes de dar por cerrado un componente grande,
releer la consigna original campo por campo y compararla explícitamente
contra lo implementado, en vez de asumir que "si no tira error, está
completo".

---

