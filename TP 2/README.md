# TP2 — Plataforma de tareas distribuidas

Computación II. Python 3.11 o superior.

## Estado del avance

Semana 1 completada: API con las cinco rutas, cola `asyncio.Queue` y tres
workers implementados como corrutinas. Los endpoints ya operan con tareas reales.
Semana 2: esperar, descargar, hashear, cancelación, timeouts, workers configurables
y apagado con volcado JSON implementados.

Las tres mediciones obligatorias están ejecutadas y documentadas abajo.
Falta completar el informe final: decisiones de diseño, conceptos del curso,
limitaciones generales y reflexión personal de la estudiante.

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
                                            tres workers
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
