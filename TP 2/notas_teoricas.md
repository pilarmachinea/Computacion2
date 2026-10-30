# Notas teóricas y bitácora — TP2 Plataforma de tareas

Este archivo reconstruye las etapas que seguimos para desarrollar el TP2.
Sirve para estudiar y explicar el código; el README es el informe de entrega.
Las etapas no representan necesariamente una clase o un día de trabajo.

## 1. Primer ejemplo: concurrencia con asyncio

### Qué hicimos

En `ejemplo_workers.py` probamos tres trabajos de dos segundos con uno y con
tres workers. Medimos aproximadamente 6,01 segundos y 2,00 segundos respectivamente.

### Conceptos teóricos

`async def` define una función de corrutina. Llamarla crea un objeto corrutina;
no ejecuta inmediatamente su cuerpo. `await` espera su resultado. Cuando una
operación debe esperar, puede suspender la corrutina y permitir que avance otra.
No todo `await` cede necesariamente el control: una operación ya lista puede
resolverse inmediatamente.

El event loop coordina las corrutinas y los eventos de I/O. En un mismo hilo
no ejecuta dos instrucciones Python a la vez: alterna el trabajo cuando las
corrutinas se suspenden. Concurrencia significa que varias tareas progresan
en períodos superpuestos; paralelismo significa ejecución simultánea.

```python
# Primero termina A; después empieza B.
await tarea("A", 3)
await tarea("B", 2)

# Ambas pueden avanzar mientras la otra espera.
await asyncio.gather(tarea("A", 3), tarea("B", 2))
```

`asyncio.sleep` deja al event loop atender otras tareas. `time.sleep` en una
corrutina bloquearía su hilo. `asyncio.run` crea y administra el event loop en
el ejemplo; en la API ese arranque lo administra Uvicorn.

**Conexión:** clase 18, corrutinas y event loop.

## 2. Cola y pool de workers

### Qué hicimos

Creamos una `asyncio.Queue` y workers con un bucle que toma y procesa trabajos.
Cada worker se programa con `asyncio.create_task`. Es una Task del event loop,
no un proceso ni un hilo propio.

### Conceptos teóricos

Es el patrón productor-consumidor: el productor encola, los consumidores retiran.
`await cola.get()` espera sin bloquear el hilo si no hay elementos. Una
`queue.Queue` pertenece al mundo de threads y su `get()` bloqueante no debe
usarse directamente dentro del event loop.

```python
trabajo = await cola.get()
try:
    await procesar(trabajo)
finally:
    cola.task_done()
```

Cada `put` aumenta un contador de trabajos sin atender. `task_done` lo reduce;
`join` espera que ese contador llegue a cero. `task_done` no retira elementos,
no cambia el estado de nuestra tarea y no significa que el trabajo tuvo éxito.
Se llama una vez por elemento obtenido, incluso si ese trabajo falla.

**Para explicar:** con tres workers y seis esperas iguales, hay dos tandas.
Más pedidos no crean automáticamente más workers.

**Conexión:** clases 20–21, colas y coordinación asíncrona.

## 3. Diferencia entre la cola y el registro de tareas

### Qué hicimos

Separamos los identificadores pendientes de los datos que necesitamos consultar:

```text
cola: identificadores pendientes
 tareas: id → datos, estado, resultado, error, duración
 ejecuciones: id → Task que está ejecutando ese trabajo
```

### Conceptos teóricos

Cuando un worker retira un identificador, deja de estar en la cola, pero su
registro permanece en el diccionario. Así se puede consultar mientras se ejecuta
y después de terminar. `trabajo = tareas[id]` obtiene una referencia al mismo
diccionario, no una copia independiente.

Los estados son pendiente, ejecutando, completada, fallida y cancelada. Un estado
terminal conserva el resultado o el motivo correspondiente.

El diccionario de registros se modifica en el hilo del event loop. Entre dos
instrucciones sin suspensión no avanza otra corrutina de ese hilo. Eso evita
necesitar un Lock en esas actualizaciones, pero no permite ignorar posibles
cambios de estado a través de un `await`.

**Conexión:** clase 18, ejecución cooperativa; clases 20–21, coordinación.

## 4. API HTTP con FastAPI

### Qué hicimos

Creamos `main.py`, los modelos de pedidos y cinco rutas:

| Ruta | Responsabilidad |
|---|---|
| POST /tareas | Validar, registrar y encolar; devolver id y estado inicial |
| GET /tareas | Listar, con filtro opcional por estado |
| GET /tareas/{identificador} | Consultar un registro |
| DELETE /tareas/{identificador} | Solicitar cancelación |
| GET /estadisticas | Contar estados, workers y duración media de completadas |

### Conceptos teóricos

HTTP es el protocolo de pedidos y respuestas. FastAPI relaciona método y ruta
con una función; Uvicorn es el servidor que recibe conexiones y ejecuta la app.
Los modelos Pydantic validan el JSON antes de que el endpoint cree el trabajo.
El `tipo` distingue los modelos de esperar, descargar y hashear.

202 significa que el pedido fue aceptado, no que el trabajo terminó. 200 significa
que una consulta o acción respondió correctamente, no que la tarea consultada
esté completada. El estado del trabajo se ve en el cuerpo JSON. 404 indica id
inexistente, 409 un conflicto al cancelar una tarea ya terminada y 422 datos
inválidos. Durante el cierre, la aplicación puede rechazar nuevos pedidos con 503.

POST no espera al trabajo: encola y responde. Un `await` que esperara toda la
ejecución dentro de ese endpoint haría perder esa propiedad.

`app.state` guarda los objetos compartidos de esta instancia. `lifespan` usa un
context manager asíncrono: antes del `yield` inicializa recursos y después los
cierra. `/docs` ofrece la interfaz para probar los pedidos.

**Conexión:** clase 19, HTTP y FastAPI; bloque 0, decoradores y context managers.

## 5. Cancelación manual

### Qué hicimos

El worker crea una Task hija para cada ejecución. DELETE cancela esa hija,
conservando el worker para otros trabajos. Si el trabajo está pendiente, marca
su registro como cancelado; el worker lo saltea al retirarlo de la cola.

### Conceptos teóricos

`Task.cancel()` solicita cancelación: no mata instantáneamente cualquier trabajo.
La corrutina recibe `CancelledError` y debe liberar recursos. `finally` se usa
para limpieza aunque haya excepción. El worker distingue su propia cancelación
por apagado de la cancelación de la hija; en el primer caso sale del bucle.

DELETE espera la limpieza antes de responder. Una segunda cancelación de un
registro ya cancelado devuelve ese registro. Si ya completó o falló, devuelve 409.

**Prueba observada:** una espera de 60 segundos se canceló a los 24,58 segundos,
con resultado y error en null. Fue una interrupción solicitada, no un fallo.

**Conexión:** clases 20–21, ciclo de vida de Tasks y cancelación.

## 6. Timeouts

### Qué hicimos

Agregamos un límite por tarea, predeterminado de 30 segundos:

```python
async with asyncio.timeout(trabajo["timeout"]):
    trabajo["resultado"] = await ejecucion
```

### Conceptos teóricos

El timeout cuenta desde la ejecución, no durante la espera en cola. Al vencer,
se cancela la ejecución y se maneja `TimeoutError`: la consigna exige registrar
`fallida` con motivo. La cancelación manual, en cambio, produce `cancelada`.

La limpieza puede agregar tiempo al límite. Esto es especialmente importante
si el recurso real vive en un proceso que necesita cooperar para detenerse.

**Para probar:** esperar 60 segundos con timeout 2; consultar después de 2 segundos.

## 7. Descargas concurrentes

### Qué hicimos

`ejecutar_descarga` crea un cliente HTTPX compartido por las URLs del trabajo,
y TaskGroup programa una corrutina `descargar_url` por URL. Se admiten 1–20 URLs.
Cada resultado incluye código HTTP, bytes, tiempo y un posible error.

### Conceptos teóricos

Es trabajo I/O-bound: gran parte del tiempo se espera a la red. El event loop
puede ocuparse de otro socket mientras una respuesta no está lista. El sistema
operativo ofrece mecanismos de multiplexación como epoll en Linux; las bibliotecas
los abstraen, no los programamos directamente en este TP.

Leer por bloques permite contar bytes sin guardar el archivo entero. `async with`
cierra la respuesta y el cliente incluso si se cancela. TaskGroup espera a las
hijas y, ante una excepción no manejada, cancela las restantes antes de salir.

Los errores de transporte de una URL se convierten en su resultado de error;
no hacen fallar todas las otras URLs. Un 404 o 500 se informa como código HTTP.
Por eso el estado completada significa que se terminó el informe de URLs, no que
todas respondieron 200. HTTPX además tiene un timeout de red de 10 segundos.

**Conexión:** clases 13–14 (sockets), 17 (multiplexación), 20–21 (I/O asíncrono).

## 8. Hashes y executor de procesos

### Qué hicimos

`calcular_hash` aplica SHA-256 N veces y devuelve el digest hexadecimal.
`ejecutar_hash` espera capacidad en un semáforo CPU y delega mediante
`run_in_executor` a un ProcessPoolExecutor. Lifespan crea un único pool con
`CPU_PROCESOS` procesos y lo cierra al apagar.

### Conceptos teóricos

Un bucle CPU-bound no se vuelve cooperativo por estar dentro de async def.
Los procesos lo ejecutan fuera del event loop, con intérpretes y GIL separados,
como recomiendan las clases 21 y 22. El costo es serializar los argumentos y
resultados e iniciar procesos. Usamos spawn y funciones a nivel de módulo.

Un Future no puede detener un cálculo ya iniciado. Cada worker tiene un evento
compartido con los procesos mediante su initializer. Al cancelar se activa;
el hash lo consulta cada 1000 iteraciones. Se espera la salida del cálculo antes
de liberar el permiso CPU y reutilizar esa señal. Los hashes esperando permiso
se cancelan en asyncio, sin entrar todavía a la cola interna del executor.
`shield` conserva la espera del resultado durante esta limpieza.

La versión inicial usaba to_thread; se reemplazó para seguir el enfoque CPU de
la cátedra y permitir paralelismo multinúcleo. Las mediciones vigentes corresponden
a procesos. to_thread sigue utilizándose para archivos y cierre del pool, no
para realizar el cálculo hash.

**Conexión:** clases 21–22, executors y cancelación.

## 9. Configuración, señales y persistencia

### Qué hicimos

`WORKERS` configura cuántas corrutinas consumidoras hay; `APAGADO_TIMEOUT` define
el plazo de cierre y `ESTADO_DIR` la carpeta de volcados. Uvicorn maneja SIGINT
(Ctrl+C) y SIGTERM e inicia el cierre del lifespan.

### Conceptos teóricos

El cierre deja de aceptar trabajos, cancela los pendientes y espera los activos
hasta el plazo. Luego cancela los restantes, espera limpieza y guarda el estado.
El JSON se escribe en un hilo para evitar I/O de archivo directamente en el loop.
Se escribe primero un temporal y después se reemplaza el destino: evita publicar
un JSON escrito parcialmente, pero no asegura persistencia ante corte de energía.

Al arrancar se informa si existe un volcado, sin restaurarlo. Persistir un archivo
y recuperar tareas son dos funcionalidades distintas. SIGKILL no permite cierre
ordenado; tampoco un corte de energía. Los estados posteriores al último volcado
pueden perderse.

**Pruebas realizadas:** SIGINT y SIGTERM, tareas que terminan dentro del plazo,
pendientes canceladas, hash interrumpido, JSON y detección al reiniciar.

## 10. Docker y Compose

### Qué hicimos

El Dockerfile usa Python 3.12, instala requirements y arranca Uvicorn en
`0.0.0.0:8000`. Compose publica el puerto y monta `datos/` del host en el contenedor.

### Conceptos teóricos

La imagen es la base para crear el contenedor; el contenedor es una instancia
en ejecución. Un entorno virtual aísla paquetes Python; Docker también proporciona
un entorno de ejecución con su sistema de archivos y aislamiento del sistema.
Los contenedores Linux comparten el kernel del host Linux (o de la VM que los aloja).

`0.0.0.0` permite recibir conexiones por las interfaces del contenedor. Publicar
el puerto las conecta con el host. El volumen conserva los JSON aunque se elimine
el contenedor. Compose deja 30 segundos de gracia: ese tiempo debe superar el
plazo de la app más el margen de limpieza y escritura.

El healthcheck consulta la API: verifica que responde, no prueba cada tipo de trabajo.
`docker compose up --build` levanta este servicio HTTP; no requiere una TUI como TP1.

## 11. Medir para comprobar

### Qué hicimos

`medir.py` usa procesos temporales, HTTP local real y tres repeticiones por escenario.
Guarda muestras en `mediciones.json`; el README interpreta sus medianas.

| Experimento | Resultado de la corrida documentada |
|---|---|
| 20 descargas secuenciales / concurrentes | 2.0870 s / 0.1464 s; speedup 14.26× |
| GET durante hash sin / con executor | 1.2536 s / 0.0014 s |
| Lote de 3 / 48 esperas con tres workers | 0.1559 s / 2.4661 s |

### Conceptos teóricos

Speedup es tiempo secuencial dividido por concurrente. Latencia es cuánto tarda
una respuesta o tarea; throughput es cuántas tareas se completan por unidad de
tiempo. No son lo mismo: el pool puede mantener su tasa mientras crece la espera.

Con tres workers y esperas de 0,15 segundos, el techo ideal es 3 / 0,15 = 20 tareas/s.
Enviar más trabajos los acumula en cola; no crea capacidad adicional. Las pruebas
no midieron agotamiento de memoria ni un límite universal del equipo.

Las descargas usan latencia simulada de 0,1 segundos y cuerpos de 1024 bytes,
transportados por HTTP real. No representan Internet. El experimento bloqueante
solo modifica el proceso de medición; la API normal mantiene el executor.

## 12. Preguntas para defender el código

- **¿Dónde podría bloquearse el loop?** En el bucle de hash si se invocara directamente;
  también al introducir I/O síncrono o `time.sleep` en sus corrutinas.
- **¿Por qué cola y diccionario?** La cola organiza pendientes; el diccionario conserva
  estados y resultados consultables después de retirarlos.
- **¿Un worker es un thread?** No: los workers son corrutinas. El executor del hash usa procesos.
- **¿Una excepción mata el pool?** Las del trabajo se capturan y el worker sigue.
  Una excepción que escape del worker puede terminarlo; no hay supervisor que lo reponga.
- **¿Qué pasa con 10.000 pedidos?** Crecen la espera y la memoria; la cola rechaza al llenarse, pero el historial no
  tiene límite y el listado no está paginado.
- **¿El timeout garantiza un corte exacto?** No: hay planificación y limpieza posterior.
- **¿El JSON permite continuar los trabajos?** No: se detecta y conserva, pero no se restaura.
- **¿Por qué no usar varios procesos Uvicorn?** Cada proceso tendría su propia cola y
  registros; una consulta podría llegar a uno que no conoce el identificador.

### Aclaraciones que conviene poder defender

**Generador, corrutina y Task.** La clase 18 explica la suspensión mediante
generadores y `send()`. Es la base conceptual, pero nuestro código no implementa
un scheduler propio ni llama `send` manualmente. Una corrutina nativa no es el
mismo tipo de objeto que un generador; una Task administra su ejecución en el loop.
El `yield` del lifespan divide inicialización y cierre, no entrega trabajos a la cola.

**Una función def no se manda siempre a un thread.** La clase 19 explica que
FastAPI delega los endpoints síncronos. Eso no se aplica automáticamente a una
función común llamada desde una corrutina. `calcular_hash(...)` ejecutado dentro
de `ejecutar_hash` bloquearía ese hilo si no se delegara explícitamente.

**Procesos para CPU, como en clase.** El pool se crea una vez, utiliza spawn
y recibe funciones importables. CPU_PROCESOS limita el cálculo independiente
del número de corrutinas WORKERS. La comparación del GET mide capacidad de
respuesta, no demuestra por sí sola el speedup de varios hashes simultáneos.

**Future de executor y Task de asyncio no se cancelan igual.** En la clase 22,
un Future del executor no puede cancelar un trabajo ya iniciado. La Task que
lo espera sí recibe CancelledError, pero el proceso continúa salvo que coopere.
Esa diferencia justifica nuestra señal de parada y la espera de limpieza.

**Cola acotada y respuesta inmediata.** Usamos Queue(maxsize=COLA_MAX).
La clase muestra esperar con put cuando se llena; nuestra API usa put_nowait
y responde 503 sin registrar el trabajo rechazado. El cliente puede reintentar.
El historial de aceptadas sigue sin límite: acotar la cola no acota todo el estado.

**Semáforo global.** DESCARGAS_MAX limita las descargas activas entre todos
los workers, mientras que 20 URLs sigue siendo el máximo de cada pedido.
El timeout cuenta también la espera por un permiso; async with lo libera ante
éxito, error o cancelación.

**gather no cancela siempre a las hermanas ante un fallo.** Por defecto propaga
la primera excepción ordinaria y las demás pueden continuar. TaskGroup sí espera
la limpieza del conjunto ante fallos. Nuestro uso de gather con
`return_exceptions=True` en el cierre sirve para recoger terminaciones después
de solicitar cancelación; no tiene la misma función que el TaskGroup de descargas.

**Cancelar un trabajo no equivale a apagar su worker.** La recomendación de la
clase 20 de propagar CancelledError se aplica a la ejecución cancelada. Nuestro
worker puede observar la cancelación de su hija y continuar atendiendo trabajos;
si se cancela al propio worker, la propaga y termina.

**Señales: Uvicorn administra el servidor.** La clase 20 instala handlers en un
servidor asyncio escrito a mano. En nuestro caso el servidor es Uvicorn y el
cierre de recursos se conecta mediante lifespan, como muestra el extra de la
clase 19. No instalamos handlers paralelos que compitan con Uvicorn.

### Orden sugerido de repaso

1. Clase 18 y etapas 1–3: explicar una espera concurrente, una cola y los estados.
2. Clase 19 y etapa 4: seguir un POST hasta su respuesta y luego un GET.
3. Clases 20–21 y etapas 5–7: seguir cancelación, timeout y limpieza de descargas.
4. Clases 21–22 y etapa 8: explicar CPU, procesos, executor y parada cooperativa.
5. Etapas 9–11: reproducir cierre, Docker y mediciones, justificando sus límites.
6. Usar las clases 13, 14 y 17 para explicar qué hay debajo de esas bibliotecas.

## 14. Ajuste al enfoque de la cátedra

Se sustituyó el executor de hilos del hash por procesos, se agregó COLA_MAX y
rechazo inmediato al llenarse, y se incorporó DESCARGAS_MAX global. Se mantuvieron
TaskGroup, lifespan y los workers asíncronos porque ya correspondían al enfoque
de clase. `verificar_recursos.py` ejercita los nuevos límites y sus cancelaciones.
Los registros rechazados no se guardan. Los procesos se cierran después de los
workers, antes de guardar JSON. Las mediciones se repitieron con esta versión.
