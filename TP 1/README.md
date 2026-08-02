# Monitor de Procesos y Threads — TP1 Computación II

> Universidad de Mendoza — Ingeniería Informática — 2026

Monitor de procesos y threads para Linux, escrito en Python con
`multiprocessing`: 9 procesos independientes (un Recolector, 7
analizadores especializados y una interfaz visual) leen y parsean
`/proc` en paralelo, se comunican mediante memoria compartida (`Manager`,
`Value`, `Queue` según el caso) y muestran en una TUI interactiva siete
vistas distintas del sistema -- Resumen, Memoria, File Descriptors,
Threads, Señales, Scheduling y Sistema -- con navegación, filtros,
ajuste de intervalos en vivo y manejo de señales del propio monitor.

---

## 1. Diagrama de arquitectura

**9 procesos independientes** (`multiprocessing.Process`), comunicados
exclusivamente a través de objetos compartidos — nunca memoria compartida
"directa" ni variables globales entre procesos:

```
┌─────────────┐   Manager().dict()
│  Recolector  │ ──── snapshot_pids ────┐
│  (Process)   │      (pid -> datos)    │
└─────────────┘                         ▼
      │ lee /proc                ┌──────────────────┐
      │ calcula CPU%              │ Analizador Resumen│──▶ Manager().list()
      ▼                          └──────────────────┘
  /proc/<pid>/*

  Los otros 6 analizadores leen /proc DIRECTAMENTE (Recolector liviano,
  cada uno trae lo específico y pesado que solo esa vista usa):

  Memoria · FDs · Threads · Señales · Scheduling  ──▶ Manager().list() c/u
  Sistema (CPU global, loadavg, meminfo, conteo)   ──▶ Manager().dict()

                          ┌─────────────────────────────────────┐
                          │              display.py               │
                          │  (Process, toma control de la TUI)    │
                          │                                        │
                          │  Thread interno: lector de teclado    │
                          │  (/dev/tty, no sys.stdin -- ver        │
                          │  limitaciones)                         │
                          └───────────────┬─────────────────────┘
                                          │ lee TODAS las vistas
                                          │ (Manager) + intervalos (Value)
                                          ▼
                                   pantalla (rich.Live)

  main.py (proceso principal):
  - Crea el Manager, los 8 Value de intervalo, la Queue de eventos.
  - Lanza los 9 procesos.
  - Loop de señales (self-pipe): SIGINT/TERM (shutdown), SIGHUP (reload
    config), SIGUSR1 (dump), SIGUSR2 (verbose).
  - Comunica eventos a display.py vía multiprocessing.Queue (cola_eventos)
    -- NUNCA con print() directo, para no pisar la pantalla que rich.Live
    controla (ver notas_teoricas.md, sesión 12).
  - Registra cada evento en monitor.log (logger_monitor.py), en paralelo
    a la cola.
```

**Tres primitivas de `multiprocessing` distintas, cada una para su caso:**
- **`Manager().dict()` / `.list()`**: estructuras de datos por proceso
  (snapshots, vistas) — múltiples lectores, un productor, datos complejos.
- **`multiprocessing.Value`**: intervalos de refresco por vista — un
  escalar simple, leído y escrito con mucha frecuencia (display ↔
  analizador), sin necesitar el overhead de un proceso servidor de
  Manager. Cada `Value` incluye lock por defecto; operaciones compuestas
  (`+=`) usan `get_lock()` explícito.
- **`multiprocessing.Queue`**: eventos puntuales de `main.py` hacia
  `display.py` (dump generado, config recargada) — paso de mensajes
  consumidos una vez, no un estado persistente a consultar repetidamente.

**Por qué `Manager` y no `Queue`/`Pipe` para las vistas de datos:** el
patrón ahí es "un productor, múltiples lectores que consultan el mismo
dato repetidas veces" — lo opuesto al caso de la cola de eventos. Un
`Manager().dict()`/`.list()` es un *proxy* serializable que habla con un
proceso servidor propio del Manager, por eso funciona igual sin importar
si los procesos hijos se lanzan con `fork` o `spawn`.

---

## 2. Decisiones de diseño

### ¿Por qué tal mecanismo de IPC y no otro?

Se eligió `Manager().dict()` / `Manager().list()` para la comunicación entre
el Recolector y los analizadores, en vez de `Queue` o `Pipe`. La razón es el
patrón de acceso: un único productor (Recolector) genera un snapshot completo
periódicamente, y varios consumidores (los analizadores) necesitan **leer el
mismo dato repetidas veces**, no consumirlo una única vez. `Queue`/`Pipe`
están pensados para paso de mensajes (el dato se envía y se consume, no
persiste); `Manager` da un objeto compartido que persiste entre lecturas,
más apropiado para este caso. Ver diagrama de arquitectura arriba.

Además, solo la vista Resumen depende del snapshot del Recolector -- los
otros 6 analizadores leen `/proc` **directamente**, sin pasar por el
Recolector. Funciones como `leer_maps`, `leer_fds` o `leer_threads` son
costosas (parsean potencialmente cientos de líneas por proceso); meterlas
todas en el Recolector pagaría ese costo en cada ciclo aunque nadie esté
mirando esa vista en particular, y ensuciaría el snapshot común con campos
que la mayoría de los analizadores no usa. Cada analizador trae únicamente
la dependencia que realmente necesita -- principio de inyección de
dependencias explícita, mejor para claridad, testeo y mantenimiento.
Trade-off aceptado: los datos entre vistas pueden no corresponder
exactamente al mismo instante, costo razonable para el alcance de este TP.

Para la comunicación puntual `main.py` → `display.py` (eventos como "dump
generado" o "config recargada"), se usa en cambio `multiprocessing.Queue`:
acá sí el patrón es "mensaje que se envía una vez y se consume", no un
estado persistente a re-consultar -- el caso exactamente opuesto al de las
vistas de datos.

### ¿Por qué `Manager` y no `Value`/`Array` para algunas cosas (o viceversa)?

Para las estructuras de datos por proceso (snapshots, vistas de cada
analizador), `Manager` es la herramienta apropiada: son diccionarios y
listas que crecen y cambian de forma, y necesitan ser leídos repetidas
veces por múltiples consumidores.

Para los **intervalos de actualización** (comunicación display ↔
analizador), en cambio, se usa `multiprocessing.Value` — según pide
explícitamente la consigna. Un intervalo es un único número (`float`) que
se lee constantemente (en cada vuelta del loop de cada analizador) y se
escribe con frecuencia (el usuario puede tocar `+`/`-` varias veces por
segundo desde el display). `Manager` levanta un proceso servidor propio
para gestionar sus proxies — overhead razonable para estructuras
complejas, pero innecesario para un escalar de acceso tan frecuente.
`Value` es memoria compartida directa (`mmap`), sin intermediarios, con
un lock incorporado por defecto (`lock=True`) que protege lecturas y
escrituras individuales; para operaciones compuestas como `+=` (que son
lectura+escritura en dos pasos) se usa `with value.get_lock():`
explícitamente para garantizar atomicidad frente a un reload concurrente
por `SIGHUP`.

### ¿Cómo se manejaron las race conditions?

Un patrón recurrente para evitarlas: nunca reasignar un objeto compartido
(`variable = nuevo_valor`), sino mutarlo en el lugar:
- `dict_compartido.update(nuevo_contenido)` en vez de reasignar la variable.
- `lista_compartida[:] = nueva_lista` (slicing asignable) en vez de reasignar.

Reasignar la variable local solo cambia a qué apunta esa referencia dentro de
la función; los demás procesos, que tienen su propia referencia al mismo
proxy compartido, no verían el cambio. Mutar el objeto sí es visible para
todos los que comparten la misma referencia.

Además, antes de iterar sobre un objeto compartido que otro proceso podría
estar modificando en simultáneo, se toma una copia local
(`list(dict_compartido.values())`) para trabajar sobre una foto consistente
del instante, evitando inconsistencias o errores de iteración sobre una
colección cambiante.

El único lugar donde se usa un `Lock` explícito (más allá del que trae
`Value` por defecto) es en el ajuste de intervalo desde `display.py`:
`+=`/`-=` sobre un `Value` son en realidad dos pasos (leer, luego
escribir), así que se envuelven con `with value.get_lock():` para
garantizar que esa operación compuesta sea atómica frente a un reload
concurrente de `SIGHUP` escribiendo el mismo `Value` al mismo tiempo. En
el resto del proyecto no hizo falta un `Lock` adicional: las estructuras
de `Manager` (`dict`/`list`) ya serializan el acceso internamente a
través de su proceso servidor, y las operaciones usadas (`.update(...)`,
slicing `[:] = ...`) son atómicas a nivel de esa API.

### ¿Por qué los intervalos elegidos por defecto?

- **Recolector: cada 1 segundo.** Es el proceso que hace el trabajo "caro"
  (leer `/proc` de todos los PIDs activos), y conviene que sea el más
  frecuente para que el resto del sistema siempre tenga datos recientes.
- **Analizadores: cada 2 segundos.** Solo leen, transforman y ordenan datos
  ya recolectados — no necesitan la misma frecuencia que el Recolector.
  Esta relación no es una regla absoluta: un analizador con cálculos más
  pesados (ej. promedios históricos) podría justificar un intervalo mayor
  aún, o incluso uno menor si necesita más resolución temporal para ese
  cálculo en particular.

### Parseo de `/proc`

- **`/proc/<pid>/stat`**: el campo `comm` (nombre del proceso) puede contener
  espacios, por lo que viene delimitado entre paréntesis. Un `split(' ')` ingenuo
  correría los índices de todos los campos siguientes. Se resolvió ubicando el
  **último** `)` de la línea con `rfind(')')` — es seguro porque no hay más
  paréntesis después del nombre — y separando ahí la línea en `comm` y el resto
  de los campos numéricos.
- **`/proc/<pid>/status`**: formato clave-valor (`Clave:\tValor`), se parsea
  línea por línea con `partition(':')`.
- **`/proc/<pid>/cmdline`**: los argumentos vienen separados por bytes nulos
  (`\x00`), no por espacios — requiere lectura en modo binario.
- **Procesos que desaparecen entre el listado y la lectura**: se captura
  `FileNotFoundError` y se devuelve `None`, tratándolo como caso esperado
  (no un error del programa).
- **Cálculo de CPU%**: `utime`/`stime` son contadores acumulados de jiffies,
  no valores instantáneos. Se calcula como delta entre dos lecturas separadas
  en el tiempo, dividido por `HZ` (`os.sysconf('SC_CLK_TCK')`) y por el tiempo
  real transcurrido. El estado de "lectura anterior por PID" se mantiene en
  memoria local del analizador (no compartida), ya que solo el resultado final
  necesita ser visible para otros procesos.

---

## 3. Limitaciones conocidas

- **El pin de PID (`Enter`) no garantiza que el proceso pineado sea
  siempre visible en todas las vistas**: cada tabla muestra solo el top
  30 de esa vista, ordenado por la métrica propia de esa vista (CPU%,
  RSS, cantidad de threads, etc.). Si el PID pineado no está entre los
  primeros 30 de una vista en particular, no aparece resaltado ahí --
  aunque el pin en sí sigue activo y se ve al volver a una vista donde
  ese proceso sí esté entre los primeros 30. Decisión de alcance
  consciente: garantizar visibilidad del pin en todas las vistas
  requeriría agregar el proceso aparte de la tabla ordenada, lo cual no
  se implementó dado el alcance de este TP.
- **Firefox (y navegadores similares) muestran `heap: 0 B`** en la vista
  Memoria: usan asignadores personalizados (jemalloc/tcmalloc) que evitan
  el `brk()`/heap clásico, haciendo casi todo con `mmap` anónimo directo
  -- por eso esa memoria se contabiliza en `shared`, no en `heap`. No es
  un bug del monitor, es una realidad de cómo los programas modernos
  manejan memoria.
- **Los datos entre vistas pueden no corresponder exactamente al mismo
  instante**: cada analizador (salvo Resumen) lee `/proc` de forma
  independiente en su propio ciclo, en vez de compartir una única lectura
  centralizada -- trade-off aceptado a cambio de un Recolector liviano y
  analizadores desacoplados (ver sección de arquitectura).
- **`contar_procesos_por_estado` hace dos lecturas de `/proc` (`stat` +
  `status`) por cada uno de los ~300+ procesos, en cada ciclo del
  analizador de Sistema** -- costo conocido y aceptado dado que esta vista
  no necesita ser la más urgente en refrescarse.
- **`docker compose up --build` inicia el monitor y muestra la TUI
  correctamente, pero el teclado no responde en ese modo.** `up`
  multiplexa la salida con el prefijo del servicio (pensado para logs de
  background, no sesiones interactivas) y el teclado no se enruta de
  forma confiable al `/dev/tty` del contenedor en ese modo, aunque
  `tty: true` y `stdin_open: true` estén correctamente seteados. **Para
  uso interactivo real hay que usar `docker compose run --rm monitor`**
  (ver sección "Cómo correr y testear"). Se documenta esto explícitamente
  en vez de ocultarlo: es una limitación real de cómo Docker Compose
  maneja stdin bajo `up` con logs multiplexados, no un bug del monitor en
  sí -- la misma imagen y el mismo `Dockerfile` funcionan perfectamente
  con `run`. Ver sesión 14 de `notas_teoricas.md` para el diagnóstico
  completo.
- **Tres bugs de sistemas operativos encontrados y corregidos durante el
  desarrollo** (documentados en detalle en `notas_teoricas.md`, sesiones
  10, 11 y 14):
  - Los procesos hijos creados con `fork()` heredan los handlers de señal
    del padre, lo que rompía `Process.terminate()` -- corregido
    restaurando `SIGTERM` a su comportamiento default (y las demás señales
    del monitor a `SIG_IGN`) al inicio de cada proceso hijo.
  - `multiprocessing` cierra `sys.stdin` en los procesos hijos (lo
    reemplaza por `/dev/null`) -- el lector de teclado del display no
    podía usar `sys.stdin` dentro de su propio proceso; se resolvió
    abriendo `/dev/tty` directamente.
  - `docker compose up` no enruta stdin de forma confiable al contenedor
    (ver arriba); se documentó el uso de `docker compose run` para
    interacción real.

---

## 4. Cómo correr y testear

### Con Docker (recomendado, forma de entrega oficial)

```bash
git clone <url-del-repo>
cd tp1-computacion2
docker compose build
docker compose run --rm monitor
```

**Importante:** usar `docker compose run`, no `docker compose up`. `up`
multiplexa la salida con el prefijo `monitor-procesos |` (pensado para
logs de servicios en background) y el teclado no se enruta de forma
confiable a `/dev/tty` del contenedor en ese modo, aunque `tty: true` y
`stdin_open: true` estén seteados. `run` da una sesión de terminal
interactiva directa, necesaria para que la TUI reciba las teclas
correctamente. `--rm` limpia el contenedor al salir.

```bash
docker kill --signal=SIGUSR1 monitor-procesos   # dump del snapshot
docker kill --signal=SIGUSR2 monitor-procesos   # toggle modo verbose
docker kill --signal=SIGHUP  monitor-procesos   # recargar config.json (bind mount, así que sí se refleja)
docker kill --signal=SIGTERM monitor-procesos   # shutdown limpio
```

Para bajar el contenedor: `docker compose down` (o `Ctrl+C` en la terminal
donde corre, seguido de `q` dentro de la TUI).

### Sin Docker (para desarrollo)

```bash
cd src
python3 -m venv ../venv
source ../venv/bin/activate
pip install -r ../requirements.txt
python3 main.py
```

Señales desde otra terminal, usando el PID que se muestra al arrancar:
```bash
kill -USR1 <PID>
kill -USR2 <PID>
kill -HUP  <PID>
kill -TERM <PID>
```

### Controles dentro de la TUI

| Tecla | Acción |
|---|---|
| `1`-`7` o `r/m/f/t/s/p/g` | Cambiar de vista (número o letra mnemotécnica) |
| `↑` / `↓` | Mover el cursor de selección |
| `Enter` | Pin/unpin del proceso bajo el cursor |
| `/` | Filtrar por nombre de comando (Enter aplica, Esc cancela) |
| `u` | Filtrar por usuario (Enter aplica, Esc cancela) |
| `c` | Ciclar criterio de orden (CPU% / RSS / PID) — vista Resumen |
| `+` / `-` | Ajustar el intervalo de refresco de la vista actual (respeta mínimo por vista) |
| `h` / `?` | Ayuda |
| `q` | Salir |

### Estructura del proyecto

```
.
├── config.json                # intervalos, mínimos, filtros default
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── README.md
├── notas_teoricas.md          # bitácora completa de sesiones (15 sesiones)
├── dudas.md
└── src/
    ├── main.py                # crea Manager/Value/Queue, lanza 9 procesos, loop de señales
    ├── procfs.py               # helpers de lectura/parseo de /proc + combinaciones por vista
    ├── cpu.py                  # CalculadoraCPU, CalculadoraCPUGlobal
    ├── procesos.py             # restaurar_senales_hijo (fix de herencia de señales en fork)
    ├── logger_monitor.py       # registrar_evento(...) -> monitor.log
    ├── recolector.py           # recolector_loop
    ├── display.py              # TUI con rich: 7 vistas, navegación, filtros, pin
    └── analizadores/
        ├── __init__.py
        ├── resumen.py
        ├── memoria.py
        ├── fds.py
        ├── threads.py
        ├── senales.py
        ├── scheduling.py
        └── sistema.py
```

**Las 7 vistas obligatorias, el Recolector, el manejo de señales del
monitor, la interfaz visual (`display.py`), y Dockerización están
completos y funcionando.**

---

## 5. Lo que aprendí haciendo este TP

- **`/proc` como filesystem virtual**: no es magia, es texto plano con
  formatos específicos (algunos amigables como `status`, otros crípticos
  como `stat`, con trampas como el `comm` entre paréntesis) que expone el
  estado interno del kernel en tiempo real.
- **Elegir la primitiva de IPC correcta no es un detalle menor**:
  `Manager`, `Value` y `Queue` resuelven problemas de comunicación
  distintos, y usar la equivocada (aunque "funcione") es una señal de no
  entender el patrón de acceso real de los datos. La migración de
  intervalos de `Manager` a `Value` en la sesión 11 fue el ejemplo más
  claro de esto.
- **Dos bugs reales de sistemas operativos, encontrados y corregidos con
  metodología, no a los ponchazos**: la herencia de handlers de señal en
  procesos creados con `fork` (rompía `Process.terminate()`), y el cierre
  de `sys.stdin` por parte de `multiprocessing` en procesos hijos (rompía
  la lectura de teclado, resuelto con `/dev/tty`). Ambos se diagnosticaron
  aislando la variable que cambiaba entre el caso que funcionaba y el que
  fallaba, no adivinando soluciones.
- **Async-signal-safety y el patrón self-pipe**: un handler de señal no es
  un lugar seguro para hacer trabajo real; solo avisar (con una operación
  mínima y segura) y dejar que el programa principal actúe en un punto
  controlado de su propia ejecución.
- **La separación de responsabilidades entre capas se paga sola**: tener
  `procfs.py` (lectura cruda) separado de `cpu.py` (cálculo derivado) y de
  los `analizadores/` (orquestación) hizo que agregar cada vista nueva
  fuera progresivamente más rápido, reusando el mismo molde.

## Estado del archivo `dudas.md`

Ver [`dudas.md`](./dudas.md) para las preguntas que quedaron sin resolver
del todo durante el desarrollo (si las hay), llevadas a clase o consulta.

## Notas de desarrollo

Ver [`notas_teoricas.md`](./notas_teoricas.md) para la bitácora completa de
sesiones de trabajo, con los conceptos teóricos de la materia conectados a
cada decisión de implementación.