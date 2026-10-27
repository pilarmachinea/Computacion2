# TP2 — Plataforma de tareas distribuidas

Computación II. Python 3.11 o superior.

## Estado del avance

Semana 1 completada: API con las cinco rutas, cola `asyncio.Queue` y tres
workers implementados como corrutinas. Los endpoints ya operan con tareas reales.
Semana 2: esperar, descargar, hashear, cancelación, timeouts, workers configurables
y apagado con volcado JSON implementados.

Este es un README inicial, no el informe final. Faltan las tres mediciones obligatorias y el informe con interpretación y reflexión personal.

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
