# TP2 — Plataforma de tareas distribuidas

Computación II. Python 3.11 o superior.

## Estado del avance

Semana 1 completada: API con las cinco rutas, cola `asyncio.Queue` y tres
workers implementados como corrutinas. Los endpoints ya operan con tareas reales.
Semana 2: esperar, descargar, hashear, cancelación, timeouts, workers configurables
y apagado con volcado JSON implementados.

Las tres mediciones obligatorias están ejecutadas y documentadas abajo.
El informe técnico incluye decisiones de diseño, conceptos del curso y limitaciones.
La reflexión personal está incorporada. La revisión de ejecución desde una copia
limpia del commit eaaec6a se realizó el 29/10/2026.

## Ejecutar en desarrollo

Desde la carpeta `TP 2`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn main:app --reload
```

Si ya existe `.venv`, basta con activarlo. Abrir http://127.0.0.1:8000/docs.
Usar un solo proceso de Uvicorn: la cola y los registros están en memoria.
Al reiniciar, los registros quedan archivados en JSON pero no se restauran en la API.
Ctrl+C detiene el servidor de forma ordenada.

## Arquitectura

```text
Cliente → POST /tareas → diccionario de registros + cola de identificadores
                                                    ↓
                                       N workers (3 por defecto)
                                                    ↓
                                  esperar / descargar / hashear
                                                    ↓
                                      actualización del registro
                                                    ↑
Cliente → GET /tareas/{identificador} ────────────────┘
```

La cola organiza el trabajo pendiente. El diccionario conserva estados y
resultados después de retirar un elemento de la cola. `task_done()` informa que
un elemento fue atendido; no modifica por sí solo el estado del registro.

## Probar las cinco rutas

Crear una tarea y copiar el `id` devuelto. Reemplazar `ID` en los comandos siguientes:

```bash
curl -X POST http://127.0.0.1:8000/tareas \
  -H 'Content-Type: application/json' \
  -d '{"tipo":"esperar","segundos":60,"timeout":120}'
curl http://127.0.0.1:8000/tareas
curl 'http://127.0.0.1:8000/tareas?estado=ejecutando'
curl http://127.0.0.1:8000/tareas/ID
curl http://127.0.0.1:8000/estadisticas
curl -X DELETE http://127.0.0.1:8000/tareas/ID
```

POST devuelve 202 sin esperar a que termine la tarea. DELETE cancela tareas
pendientes o en ejecución; devuelve 409 si ya se completaron o fallaron.
Un identificador inexistente devuelve 404 y los datos inválidos devuelven 422.
El timeout cuenta desde el inicio de ejecución, no desde el ingreso a la cola.

## Verificar la semana 1

```bash
python verificar_semana1.py
```

La prueba usa la API en memoria sin tocar el servidor abierto ni sus registros.
Comprueba las cinco rutas, tres trabajos ejecutándose y un cuarto en cola,
cancelación y reutilización de workers. No sustituye las mediciones obligatorias.

## Configuración y apagado

```bash
WORKERS=4 APAGADO_TIMEOUT=5 ESTADO_DIR=datos uvicorn main:app
```

`WORKERS` configura las corrutinas consumidoras (predeterminado: 3), no procesos
Uvicorn. `APAGADO_TIMEOUT` es el plazo en segundos para terminar los trabajos en
curso (predeterminado: 10). `ESTADO_DIR` indica dónde guardar los volcados.

Uvicorn maneja SIGINT (Ctrl+C) y SIGTERM e inicia el cierre del lifespan.
La aplicación rechaza nuevas tareas, cancela las pendientes y espera las que
están en ejecución. Al vencer el plazo cancela las restantes y espera su limpieza;
esta limpieza y la escritura en disco pueden agregar tiempo al plazo configurado.
Finalmente guarda `datos/estado_<timestamp>.json`, con fecha UTC y registros.
La escritura se hace en un hilo y reemplaza un archivo temporal al completarse.
Al arrancar informa por log si encuentra un volcado previo, sin restaurarlo.
Los volcados pueden contener textos y URLs enviados: son archivos locales ignorados por Git.

## Ejecutar con Docker

Desde `TP 2`, con Docker y Docker Compose instalados:

```bash
docker compose up --build
```

Abrir http://localhost:8000/docs. No requiere activar `.venv` ni instalar Python
en el host. Detener primero el Uvicorn local si está usando el puerto 8000.
Para usar otro puerto: `API_PORT=8001 docker compose up --build`.

Ctrl+C detiene los contenedores y permite el apagado ordenado. También se puede
usar `docker compose down` desde otra terminal en la misma carpeta.
Los JSON quedan en `datos/` del host mediante un volumen bind; sobreviven a
eliminar el contenedor. Si se ejecuta Docker como root, pueden pertenecer a root.

`WORKERS` y `APAGADO_TIMEOUT` se pueden configurar como en la ejecución local.
Compose espera 30 segundos antes de forzar el cierre. Si se aumenta el plazo de
la aplicación, también se debe aumentar `STOP_GRACE_PERIOD` para dejar margen
para limpieza y escritura. Ejemplo:

```bash
WORKERS=4 APAGADO_TIMEOUT=20 STOP_GRACE_PERIOD=40s docker compose up --build
```

La imagen usa un solo proceso Uvicorn sin recarga automática. El healthcheck
consulta `/estadisticas` para comprobar que la API responde.

## Mediciones obligatorias

Corrida: 2026-10-28T18:17:24.016160+00:00. Python 3.12.3, Linux x86_64,
8 CPU lógicas. Ejecución local fuera de Docker: 3 workers,
2 procesos CPU (spawn), cola de 100 y máximo global de 20 descargas.
Cada escenario se repitió tres veces; se muestran medianas. Las muestras están
en [mediciones.json](mediciones.json) y el procedimiento en [medir.py](medir.py).

```bash
python medir.py
```

El script inicia servidores temporales en puertos libres y los cierra al finalizar.
Reemplaza mediciones.json; si se repite, también deben actualizarse las tablas.
El modo bloqueante solo existe en el servidor experimental, no modifica main.py.

### 1. Concurrencia I/O

20 descargas HTTP reales de 1024 bytes desde un proceso local que espera 0,1 s
por respuesta. Se usa descargar_url secuencialmente y ejecutar_descarga con
TaskGroup y un semáforo de 20. Se verifica código 200, tamaño y ausencia de errores.
Ambas variantes incluyen creación y cierre del cliente. La demora está simulada:
no es una medición de Internet.

| Ejecución | Tiempo |
|---|---:|
| Secuencial | 2.0870 s |
| Concurrente | 0.1464 s |
| Speedup | 14.26× |

Las esperas se superponen. El resultado concurrente incluye costos de conexiones,
planificación y cliente, por eso el speedup no llega al ideal de 20.

### 2. Event loop bloqueado

Hash de 3.000.000 iteraciones sobre hola. En la variante bloqueante se llama
calcular_hash en el loop; en la normal se usa run_in_executor con procesos.
El cliente externo espera una marca escrita justo antes del cálculo: en la
variante normal la escribe el proceso hijo, evitando medir solamente el arranque
del pool. Se espera el resultado de cada hash antes de comenzar otra repetición.

| Variante | Latencia GET /tareas | Estado al responder |
|---|---:|---|
| Sin executor | 1.2536 s | completada |
| ProcessPoolExecutor | 0.0014 s | ejecutando |

El GET respondió 904.7 veces más rápido con executor. El cálculo fuera
del loop permite responder antes de que termine. Esta prueba mide respuesta de
la API con un hash activo; no mide el speedup CPU de múltiples hashes paralelos.
El arranque de procesos y la serialización tienen costo, aunque la marca evita
incluir el arranque en la latencia GET.

### 3. Techo del pool

Lotes concurrentes de esperas de 0,15 s con tres workers. Se mide desde el inicio
del envío hasta observar finalización, incluyendo HTTP y cola. El sondeo de GET
se repite aproximadamente cada 5 ms y agrega margen a las latencias. No se mezclan
lotes activos. Cada fila resume tres repeticiones.

| Tareas | Tiempo total (s) | Latencia media observada (s) | Tareas/s |
|---:|---:|---:|---:|
| 1 | 0.1565 | 0.1565 | 6.39 |
| 3 | 0.1559 | 0.1559 | 19.24 |
| 6 | 0.3101 | 0.2342 | 19.35 |
| 12 | 0.6161 | 0.3884 | 19.48 |
| 24 | 1.2278 | 0.6974 | 19.55 |
| 48 | 2.4661 | 1.3231 | 19.46 |

Superar tres trabajos agrega espera en cola. La capacidad se aproxima a 20 tareas/s
(3 / 0,15); aumentar pedidos no crea más capacidad. Esta carga no llena la cola de
100, por lo que mide saturación del servicio y no rechazo por admisión. La prueba
verificar_recursos.py sí verifica cola llena y respuesta 503. El historial sigue
sin límite y puede consumir memoria con cargas sostenidas. Estos resultados no
son un límite físico universal del equipo.

## Ejemplos de los tres tipos de tarea

```bash
curl -X POST http://localhost:8000/tareas -H 'Content-Type: application/json' \
  -d '{"tipo":"esperar","segundos":2,"timeout":10}'
curl -X POST http://localhost:8000/tareas -H 'Content-Type: application/json' \
  -d '{"tipo":"descargar","urls":["https://example.com","https://www.python.org"],"timeout":30}'
curl -X POST http://localhost:8000/tareas -H 'Content-Type: application/json' \
  -d '{"tipo":"hashear","texto":"hola","iteraciones":1000000,"timeout":30}'
```

Cada respuesta contiene un identificador que se consulta con GET. Para comprobar
el timeout, enviar una espera de 60 segundos con `timeout: 2`: termina como
`fallida` con el motivo. Para comprobar cancelación manual, usar `timeout: 120`
y enviar DELETE antes de que termine la espera.

## Decisiones de diseño

### Cola asíncrona y estado compartido

Se usa `asyncio.Queue` porque `await cola.get()` suspende la corrutina cuando
no hay trabajos, permitiendo avanzar al event loop. `queue.Queue.get()` es una
operación bloqueante diseñada para threads: ejecutarla directamente en el hilo
de la API podría detener el servidor mientras espera elementos.

El endpoint POST registra el trabajo y encola su identificador. No ejecuta el
trabajo ni espera `cola.join()`, por eso devuelve 202 inmediatamente después de
encolarlo. Los workers consumen la cola y actualizan el diccionario compartido.
`task_done()` se ejecuta en `finally`, incluso ante fallos y cancelaciones.

Los registros se modifican en el hilo del event loop. Los bloques de actualización
sin `await` no se intercalan con otras corrutinas, por lo que no necesitan un Lock
para esas operaciones. Esto no convierte cualquier código asíncrono en atómico:
un bloque que contiene esperas debe considerar cambios de estado entre ellas.
El proceso del hash no modifica el diccionario; devuelve un valor y consulta un
`multiprocessing.Event` para recibir la señal de cancelación.

### I/O y cálculo CPU

`httpx.AsyncClient` permite esperar respuestas de red sin bloquear el event loop.
Un cliente por tarea se comparte entre sus URLs y se cierra al finalizar. Las
respuestas se leen por bloques para contar bytes sin almacenar todo el archivo.
El tamaño informado corresponde a los bytes del contenido entregado por HTTPX
tras su decodificación, no al tráfico total sobre la red.

`TaskGroup` inicia las descargas concurrentemente, espera a sus hijas y propaga
la cancelación. Un `for` que espera cada descarga antes de iniciar la siguiente
sería secuencial. Se eligió TaskGroup para asegurar la limpieza del conjunto ante
fallos inesperados, además de ejecutar concurrentemente como permite `gather`.

Un HTTP 404 o 500 se devuelve como código en el resultado de esa URL; no se llama
a `raise_for_status()`. Los errores de transporte se guardan en el campo `error`
de cada URL y las demás descargas continúan. Por eso `completada` para una tarea
de descargas significa que se elaboró el informe, no que todas sus URLs dieron 200.

`calcular_hash` aplica SHA-256 al texto UTF-8 y después a los bytes del digest
anterior. El cálculo se delega con `run_in_executor` a un ProcessPoolExecutor
creado una vez en lifespan. Se usa el contexto spawn en todos los sistemas y una
función a nivel de módulo para que pueda importarse en los hijos. Cada proceso
tiene su intérprete: los hashes pueden aprovechar varios núcleos sin competir
por un único GIL. Los argumentos y resultados se serializan, con un costo adicional.

Un semáforo CPU limita el envío al número de procesos disponibles. Los trabajos
que esperan capacidad quedan suspendidos en asyncio y se pueden cancelar sin
esperar que otros cálculos terminen. Cada worker tiene un multiprocessing.Event
compartido con los procesos mediante el initializer. No se reutiliza su señal
hasta que el cálculo anterior termina. Ctrl+C se coordina en Uvicorn; los hijos
ignoran SIGINT y salen de sus cálculos mediante esas señales.

### Cancelación, timeout y recursos

Cada worker crea una Task hija para el trabajo actual. DELETE cancela esa hija,
no el worker. Una tarea pendiente queda marcada como cancelada y se saltea al
retirarla de la cola. Una tarea ya completada o fallida devuelve 409.

Al cancelar una descarga, TaskGroup cancela sus corrutinas hijas y los bloques
`async with` cierran las respuestas y el cliente. Al cancelar un hash, la función
asíncrona activa la señal de parada y espera la salida del cálculo en el proceso. El bucle consulta
la señal cada 1000 iteraciones. `shield` permite conservar la espera del cálculo
para completar su limpieza: cancelar un Future no detiene un cálculo ya iniciado.

`asyncio.timeout` limita el trabajo desde que empieza a ejecutarse. Su vencimiento
cancela la ejecución y se registra como `fallida` con motivo. La cancelación manual
se registra como `cancelada`. El timeout predeterminado por tarea es 30 segundos;
HTTPX además tiene un límite de 10 segundos para sus operaciones de red.
La duración puede superar ligeramente el límite mientras se liberan recursos.

### Excepciones de los workers

Las excepciones ordinarias del trabajo se capturan, el registro pasa a `fallida`
y el worker vuelve a la cola. `CancelledError` tiene manejo separado para
distinguir cancelación del trabajo de cancelación del worker durante el apagado.

Si una excepción escapa del bucle del worker —por ejemplo, un error fuera del
bloque protegido— esa Task termina y el pool pierde capacidad. No hay supervisor
que la reinicie. En el cierre se recogen las terminaciones con `gather`, pero eso
no sustituye la supervisión durante la ejecución. Es una limitación de esta versión.

## Conceptos del curso aplicados

La relación siguiente usa las referencias de clases indicadas en la consigna:

| Clases | Concepto | Aplicación en el código |
|---|---|---|
| 13–14 | Sockets TCP y servidores concurrentes | Uvicorn recibe HTTP sobre sockets; HTTPX abre conexiones para las descargas. No se implementa TCP manualmente. |
| 17 | Multiplexación de I/O (`select`, `epoll`) | El event loop puede atender sockets listos mientras otras conexiones esperan; las bibliotecas abstraen el mecanismo del sistema. |
| 18 | Corrutinas y event loop | `worker`, `ejecutar_espera` y los endpoints usan `async def`; `await` permite suspender sin bloquear el hilo. |
| 19 | HTTP y FastAPI | Rutas, JSON, validación de pedidos y códigos 202, 404, 409, 422 y 503. |
| 20–21 | Asyncio en red, colas y cancelación | `AsyncClient`, `asyncio.Queue`, TaskGroup, cancelación y timeout. |
| 22 | Executors y `concurrent.futures` | `run_in_executor` delega el hash a un ProcessPoolExecutor. |
| Bloque 0 | Context managers y decoradores | `asynccontextmanager` define el lifespan; `async with` libera recursos y los decoradores registran rutas. |

`asyncio.sleep` suspende una corrutina. `time.sleep` dentro de una corrutina
bloquearía el hilo del event loop: por eso no se usa en los trabajos de la API.
El script de mediciones usa esperas síncronas en su cliente externo, que es otro
proceso y no el event loop del servidor medido.

## Limitaciones conocidas

- El estado vive en un único proceso. Los volcados se detectan pero no se restauran;
  un cierre forzado como SIGKILL o un corte de energía puede perder cambios.
- La cola está acotada y rechaza con 503 al llenarse. El historial de tareas
  aceptadas sigue sin límite y GET no está paginado: puede crecer la memoria
  después de muchos pedidos aunque la cola nunca supere su capacidad.
- Cada trabajo admite hasta 20 URLs y el semáforo limita globalmente las
  descargas activas. No hay límite total de bytes por URL.
- Los procesos compiten por CPU y memoria; serializar e iniciar procesos tiene
  costo. Más workers o procesos no garantizan mayor rendimiento.
- No hay autenticación, rate limiting ni restricciones de destinos HTTP. Es un
  servicio académico para ejecución controlada, no un servicio público preparado
  para aceptar URLs arbitrarias de usuarios externos.
- No hay reintentos ni supervisión que reponga workers caídos.
- La escritura JSON puede fallar por permisos o falta de espacio. El log informa
  el fallo de apagado; el archivo temporal evita publicar un JSON parcial, pero
  no garantiza durabilidad ante fallas de energía.
- El plazo de apagado limita la espera de trabajos; limpieza y escritura agregan
  tiempo. Compose debe permitir suficiente margen antes de enviar SIGKILL.
- Las mediciones son locales y con cargas controladas. No caracterizan Internet,
  todas las cargas posibles ni los límites físicos absolutos de la máquina.

## Lo que aprendí

Con este TP terminé de entender mejor cómo funciona la programación asíncrona. Antes entendía más o menos qué hacían `async` y `await`, pero al tener que usarlos en un programa me quedó mucho más claro cómo se pueden manejar varias tareas sin que una bloquee a las demás.

Lo que más me costó fue entender cómo funcionaban los workers y la cola de tareas, sobre todo por qué necesitábamos guardar las tareas en un diccionario si ya estaban en la cola. También me costó un poco entender cuándo usar `task_done()` y `join()`, pero a medida que fui probando el código lo fui entendiendo mejor.

En las pruebas me sorprendió ver cómo se iban procesando las tareas y que, aunque una fallara, las demás podían seguir ejecutándose. Creo que ver el programa funcionando me ayudó mucho más que solamente leer la teoría.

## Límites de recursos y pruebas de regresión

| Variable | Predeterminado | Qué limita |
|---|---:|---|
| WORKERS | 3 | Trabajos tomados por corrutinas consumidoras |
| CPU_PROCESOS | 2 | Cálculos CPU enviados a procesos simultáneamente |
| COLA_MAX | 100 | Identificadores pendientes en la cola |
| DESCARGAS_MAX | 20 | Descargas HTTP activas entre todos los trabajos |

`put_nowait` rechaza inmediatamente con 503 y Retry-After cuando la cola está
llena. No deja un registro del pedido rechazado. Es control de admisión: a
diferencia de esperar con `put`, preserva la respuesta rápida de POST.
Una tarea cancelada aún encolada ocupa su lugar hasta que un worker la retira.
El semáforo de descargas se comparte en app.state; el timeout incluye la espera
por un permiso. Los permisos se liberan al completar, fallar o cancelar.

```bash
python verificar_recursos.py
```

Comprueba cola llena, ausencia de registros de pedidos rechazados, hash conocido,
cancelación de hashes activos y esperando capacidad, timeout, reutilización,
límite de descargas entre dos trabajos y cierre sin procesos hijos vivos.

## Revisión de entrega (29/10/2026)

Se clonó el commit eaaec6a en una carpeta temporal, sin el entorno virtual ni
los archivos no versionados del proyecto. Pasaron verificar_semana1.py y
verificar_recursos.py usando el intérprete del entorno de desarrollo. Además,
Docker Compose construyó e inició esa copia con sus propias dependencias;
se comprobaron esperar, descargar y hashear por HTTP, apagado con JSON y
detección del volcado al reiniciar. Los contenedores de revisión se eliminaron.

El repositorio público es https://github.com/pilarmachinea/Computacion2.
Para ejecutar después de clonarlo:

```bash
git clone https://github.com/pilarmachinea/Computacion2.git
cd "Computacion2/TP 2"
docker compose up --build
```

Las notas teóricas son material personal de estudio y no se requieren para
construir la imagen ni ejecutar la API.
