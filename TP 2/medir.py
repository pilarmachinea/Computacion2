"""Mediciones reproducibles por HTTP local; no modifica el servidor habitual."""
import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

import httpx

ROOT = Path(__file__).resolve().parent


@contextmanager
def servidor(modo, carpeta):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        puerto = sock.getsockname()[1]
    env = dict(os.environ, WORKERS='3', APAGADO_TIMEOUT='1', ESTADO_DIR=str(carpeta / 'estados'))
    with (carpeta / f'{modo}.log').open('w') as log:
        proceso = subprocess.Popen(
            [sys.executable, '-B', str(Path(__file__).resolve()), '--servidor', modo,
             '--puerto', str(puerto), '--marca', str(carpeta / 'inicio')],
            env=env, stdout=log, stderr=log, cwd=ROOT,
        )
        try:
            url = f'http://127.0.0.1:{puerto}'
            with httpx.Client(timeout=1) as cliente:
                for _ in range(100):
                    try:
                        cliente.get(url + '/openapi.json').raise_for_status()
                        break
                    except httpx.HTTPError:
                        if proceso.poll() is not None:
                            raise RuntimeError((carpeta / f'{modo}.log').read_text())
                        time.sleep(.05)
                else:
                    raise RuntimeError('El servidor no inició')
            yield url
        finally:
            proceso.terminate()
            try:
                proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proceso.kill()
                proceso.wait()
                raise RuntimeError('El servidor de medición no cerró ordenadamente')


def iniciar_servidor(args):
    import uvicorn
    if args.servidor == 'origen':
        from fastapi import FastAPI, Response
        app = FastAPI()

        @app.get('/archivo')
        async def archivo():
            await asyncio.sleep(.1)
            return Response(b'x' * 1024, media_type='application/octet-stream')
    else:
        import main
        original = main.ejecutar_hash

        async def hash_medido(trabajo):
            # Marca para que el cliente envíe GET mientras el cálculo está activo.
            Path(args.marca).write_text('inicio')
            if args.servidor == 'bloqueado':
                valor = main.calcular_hash(trabajo['texto'], trabajo['iteraciones'], threading.Event())
                return {'hash': valor}
            return await original(trabajo)

        main.ejecutar_hash = hash_medido
        app = main.app
    uvicorn.run(app, host='127.0.0.1', port=args.puerto, access_log=False)


async def medir_io(url):
    from main import descargar_url, ejecutar_descarga
    urls = [url + '/archivo'] * 20
    muestras = []
    for _ in range(3):
        inicio = time.perf_counter()
        async with httpx.AsyncClient() as cliente:
            sec = [await descargar_url(cliente, u) for u in urls]
        sec_t = time.perf_counter() - inicio
        inicio = time.perf_counter()
        con = await ejecutar_descarga({'urls': urls})
        con_t = time.perf_counter() - inicio
        assert all(r['codigo_estado'] == 200 and r['tamano_bytes'] == 1024 and not r['error'] for r in sec + con)
        muestras.append({'secuencial_s': sec_t, 'concurrente_s': con_t})
    return muestras


def esperar_fin(cliente, identificador):
    limite = time.monotonic() + 60
    while time.monotonic() < limite:
        r = cliente.get('/tareas/' + identificador)
        r.raise_for_status()
        tarea = r.json()
        if tarea['estado'] in ('completada', 'fallida', 'cancelada'):
            assert tarea['estado'] == 'completada', tarea
            return tarea
        time.sleep(.01)
    raise RuntimeError('Tarea no terminó')


async def medir_pool(url):
    resultados = []
    async with httpx.AsyncClient(base_url=url, timeout=30) as cliente:
        for cantidad in (1, 3, 6, 12, 24, 48):
            for repeticion in range(3):
                inicio = time.perf_counter()
                async def enviar():
                    r = await cliente.post('/tareas', json={'segundos': .15})
                    r.raise_for_status()
                    return r.json()['id']
                ids = set(await asyncio.gather(*(enviar() for _ in range(cantidad))))
                latencias = {}
                while len(latencias) < cantidad:
                    r = await cliente.get('/tareas')
                    r.raise_for_status()
                    for t in r.json():
                        if t['id'] in ids and t['estado'] == 'completada':
                            latencias.setdefault(t['id'], time.perf_counter() - inicio)
                        if t['id'] in ids:
                            assert t['estado'] not in ('fallida', 'cancelada')
                    if time.perf_counter() - inicio > 30:
                        raise RuntimeError('Pool no completó el lote')
                    await asyncio.sleep(.005)
                total = max(latencias.values())
                resultados.append({'cantidad': cantidad, 'repeticion': repeticion + 1,
                                   'total_s': total, 'latencia_media_s': statistics.mean(latencias.values()),
                                   'tareas_por_s': cantidad / total})
    return resultados


def ejecutar():
    datos = {'fecha_utc': datetime.now(timezone.utc).isoformat(),
             'python': sys.version, 'sistema': platform.platform(), 'cpu_logicas': os.cpu_count(),
             'workers': 3, 'iteraciones_hash': 3_000_000, 'repeticiones': 3}
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        with servidor('origen', carpeta) as url:
            datos['io'] = asyncio.run(medir_io(url))
        print('Descargas medidas.', flush=True)
        datos['cpu'] = {}
        for modo in ('bloqueado', 'executor'):
            with servidor(modo, carpeta) as url, httpx.Client(base_url=url, timeout=60) as cliente:
                muestras = []
                for _ in range(3):
                    marca = carpeta / 'inicio'
                    marca.unlink(missing_ok=True)
                    r = cliente.post('/tareas', json={'tipo':'hashear','texto':'hola','iteraciones':3_000_000,'timeout':60})
                    r.raise_for_status()
                    id = r.json()['id']
                    limite = time.monotonic() + 10
                    while not marca.exists():
                        if time.monotonic() > limite: raise RuntimeError('No comenzó el hash')
                        time.sleep(.001)
                    inicio = time.perf_counter()
                    respuesta = cliente.get('/tareas')
                    latencia = time.perf_counter() - inicio
                    respuesta.raise_for_status()
                    estado = next(t['estado'] for t in respuesta.json() if t['id'] == id)
                    tarea = esperar_fin(cliente, id)
                    muestras.append({'get_s': latencia, 'estado_al_responder': estado,
                                     'ejecucion_hash_s': tarea['duracion_segundos']})
                datos['cpu'][modo] = muestras
            print(f'CPU {modo} medida.', flush=True)
        with servidor('executor', carpeta) as url:
            datos['pool'] = asyncio.run(medir_pool(url))
        print('Pool medido.', flush=True)
    salida = ROOT / 'mediciones.json'
    salida.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + '\n')
    print(f'Resultados guardados en {salida}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--servidor', choices=['origen', 'bloqueado', 'executor'])
    parser.add_argument('--puerto', type=int)
    parser.add_argument('--marca')
    args = parser.parse_args()
    if args.servidor:
        iniciar_servidor(args)
    else:
        ejecutar()
