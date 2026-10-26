# TP2 — Plataforma de tareas distribuidas

Computación II. Python 3.11 o superior.

## Estado del avance

Semana 1 completada: API con las cinco rutas, cola `asyncio.Queue` y tres
workers implementados como corrutinas. Los endpoints ya operan con tareas reales.
También se avanzó en la semana 2: esperar, descargar, hashear, cancelación y timeouts.

Este es un README inicial, no el informe final. Faltan workers configurables,
apagado con plazo y volcado JSON, detección de volcados al iniciar, Docker,
las tres mediciones obligatorias y el informe con interpretación y reflexión personal.

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
Al reiniciar o recargar se pierden los registros. Ctrl+C detiene el servidor.

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
