# TP2 — Plataforma de tareas distribuidas

Computación II. Python 3.11 o superior.

## Estado del avance

Semana 1 completada: API con las cinco rutas, cola `asyncio.Queue` y tres
workers implementados como corrutinas. Los endpoints ya operan con tareas reales.
Semana 2: esperar, descargar, hashear, cancelación, timeouts, workers configurables
y apagado con volcado JSON implementados.

Las tres mediciones obligatorias están ejecutadas y documentadas abajo.
El informe técnico incluye decisiones de diseño, conceptos del curso y limitaciones.
La reflexión personal está incorporada. Queda pendiente la revisión final del repositorio antes de entregar.

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

Resultados ejecutados el 2026-10-27T23:56:09.363874+00:00, en Linux x86_64, Python 3.12.3,
con 8 CPU lógicas disponibles. Ejecución local fuera de Docker,
un proceso Uvicorn por servidor y tres workers. Cada escenario se repitió tres
veces; las tablas muestran medianas. Las muestras originales están en
[mediciones.json](mediciones.json) y el procedimiento en [medir.py](medir.py).

Para repetir desde el entorno virtual:

```bash
python medir.py
```

El script crea servidores en puertos locales libres, usa carpetas temporales
para los volcados y los detiene al terminar. No utiliza el servidor de desarrollo.
El modo bloqueante existe solo en el proceso de medición: no cambia `main.py`.
Al repetir, se reemplaza `mediciones.json`; actualizar estas tablas si cambian los resultados.

### 1. Concurrencia I/O

Veinte descargas HTTP de 1024 bytes cada una desde un servidor local en otro
proceso. Cada respuesta espera 0,1 segundos con `asyncio.sleep` para simular latencia.
Se utiliza la misma función `descargar_url` del sistema. La versión secuencial
hace un `for` con `await`; la concurrente usa `ejecutar_descarga` con TaskGroup.
Ambas mediciones incluyen crear y cerrar el cliente HTTP. Se verifican códigos
200, tamaños y ausencia de errores. Son descargas reales por sockets locales,
con demora simulada; no representan el rendimiento de Internet.

| Ejecución | Tiempo de 20 descargas |
|---|---:|
| Secuencial | 2.0994 s |
| Concurrente | 0.1564 s |
| Speedup (secuencial / concurrente) | 13.42× |

Las esperas se superponen: la duración concurrente se aproxima a una respuesta
más el costo de conexiones, cliente y planificación. El speedup es menor que
20 por esos costos, pero evidencia concurrencia real de I/O.

### 2. Event loop bloqueado

Una tarea de 3.000.000 iteraciones SHA-256 sobre el texto `hola`. En el proceso
experimental bloqueante se llama directamente a `calcular_hash` desde una
corrutina; en el normal se utiliza `asyncio.to_thread`. Una marca al inicio del
cálculo avisa al cliente externo cuándo enviar `GET /tareas`. Se espera que
termine cada hash antes de comenzar la siguiente repetición.

| Variante | Latencia de GET /tareas | Estado al responder |
|---|---:|---|
| Sin executor | 1.1383 s | completada |
| Con executor de hilos | 0.0160 s | ejecutando |

El GET respondió aproximadamente 71.2 veces más rápido con executor.
Sin executor, la API solo pudo responder después del cálculo. Con el hilo,
respondió mientras el trabajo seguía ejecutándose. Esto no demuestra aceleración
CPU ni paralelismo multinúcleo: el GIL y la competencia por CPU siguen afectando
la latencia. El objetivo aquí es mantener el event loop disponible.

### 3. Techo del pool

Lotes de tareas `esperar` de 0,15 segundos enviados concurrentemente a un pool
fijo de tres workers. El tiempo se mide desde el comienzo del envío del lote,
incluyendo HTTP, espera en cola y ejecución. La finalización se detecta consultando
`GET /tareas` aproximadamente cada 5 ms; esta observación añade margen a las
latencias. Cada fila es la mediana de tres lotes; no se mezclan lotes activos.

| Tareas del lote | Tiempo total (s) | Latencia media observada (s) | Tareas/s |
|---:|---:|---:|---:|
| 1 | 0.1624 | 0.1624 | 6.16 |
| 3 | 0.1647 | 0.1647 | 18.22 |
| 6 | 0.3168 | 0.2420 | 18.94 |
| 12 | 0.6245 | 0.3977 | 19.22 |
| 24 | 1.2326 | 0.7062 | 19.47 |
| 48 | 2.4559 | 1.3160 | 19.54 |

La degradación por cola aparece al superar tres trabajos simultáneos: los
workers deben atenderlos en tandas. Entre 3 y 48 trabajos la tasa se aproxima
a 20 tareas/s, el techo teórico de esta carga (3 / 0,15). Aumentar solicitudes
no aumenta esa capacidad: aumenta el tiempo hasta terminar y la latencia media.
No es un límite universal de la máquina ni una medición de saturación CPU.
Con descargas reales o hashes el límite dependerá además de red, GIL y recursos.
La cola y el historial no tienen límite: cargas sostenidas superiores a la tasa
de servicio pueden agotar memoria. Esta prueba finita no mide ese agotamiento.

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
El hilo del hash no modifica el diccionario; devuelve un valor y consulta una
`threading.Event` para recibir la señal de cancelación.

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
anterior; solo el último digest se convierte a hexadecimal. El bucle es síncrono
y CPU-bound. Se invoca mediante `asyncio.to_thread`, que utiliza el executor de
hilos, para no ejecutarlo directamente en el event loop. Esto está permitido por
la consigna. El GIL limita el paralelismo CPU y puede aumentar la latencia: la
medición demuestra capacidad de respuesta, no aceleración de los hashes.
Un ProcessPoolExecutor sería una alternativa para paralelismo multinúcleo,
a costa de comunicación entre procesos y una cancelación más compleja.

### Cancelación, timeout y recursos

Cada worker crea una Task hija para el trabajo actual. DELETE cancela esa hija,
no el worker. Una tarea pendiente queda marcada como cancelada y se saltea al
retirarla de la cola. Una tarea ya completada o fallida devuelve 409.

Al cancelar una descarga, TaskGroup cancela sus corrutinas hijas y los bloques
`async with` cierran las respuestas y el cliente. Al cancelar un hash, la función
asíncrona activa la señal de parada y espera la salida del hilo. El bucle consulta
la señal cada 1000 iteraciones. `shield` permite conservar la espera del cálculo
para completar su limpieza: cancelar una Task no mata un hilo automáticamente.

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
| 22 | Executors y `concurrent.futures` | `to_thread` delega el hash a un executor de hilos. |
| Bloque 0 | Context managers y decoradores | `asynccontextmanager` define el lifespan; `async with` libera recursos y los decoradores registran rutas. |

`asyncio.sleep` suspende una corrutina. `time.sleep` dentro de una corrutina
bloquearía el hilo del event loop: por eso no se usa en los trabajos de la API.
El script de mediciones usa esperas síncronas en su cliente externo, que es otro
proceso y no el event loop del servidor medido.

## Limitaciones conocidas

- El estado vive en un único proceso. Los volcados se detectan pero no se restauran;
  un cierre forzado como SIGKILL o un corte de energía puede perder cambios.
- La cola y el historial crecen sin límite. Con 10.000 pedidos puede aumentar la
  espera y el consumo de memoria; GET devuelve la lista completa sin paginación.
- Cada trabajo admite hasta 20 URLs, pero no existe un límite global de descargas
  independiente del número de workers. No hay límite total de bytes por URL.
- Los hashes compiten por CPU y GIL. Más workers no garantizan mayor rendimiento.
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
