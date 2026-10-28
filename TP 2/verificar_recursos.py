"""Pruebas de procesos, backpressure y límite global de descargas."""
import asyncio
import hashlib
import multiprocessing
import os
import tempfile
from unittest.mock import patch

import httpx
import main


async def verificar():
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
        'WORKERS':'3', 'CPU_PROCESOS':'1', 'COLA_MAX':'1',
        'DESCARGAS_MAX':'2', 'APAGADO_TIMEOUT':'0.01', 'ESTADO_DIR':tmp,
    }):
        async with main.app.router.lifespan_context(main.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as api:
                async def enviar(pedido):
                    r=await api.post('/tareas',json=pedido)
                    assert r.status_code==202,r.text
                    await asyncio.sleep(.01)
                    return r.json()['id']
                async def terminar():
                    await asyncio.wait_for(main.app.state.cola.join(),10)
                ids=[await enviar({'segundos':60,'timeout':120}) for _ in range(4)]
                r=await api.post('/tareas',json={'segundos':1})
                assert r.status_code==503
                assert len(main.app.state.tareas)==4  # El rechazo no deja registro.
                for id in reversed(ids):
                    assert (await api.delete('/tareas/'+id)).json()['estado']=='cancelada'
                await terminar()
                pedido={'tipo':'hashear','texto':'hola','iteraciones':2}
                id=await enviar(pedido); await terminar()
                esperado=hashlib.sha256(hashlib.sha256(b'hola').digest()).hexdigest()
                assert main.app.state.tareas[id]['resultado']['hash']==esperado
                largo=dict(pedido,iteraciones=100_000_000,timeout=120)
                ids=[await enviar(largo) for _ in range(3)]
                # Un proceso y tres workers: incluye cálculos esperando al executor.
                for id in reversed(ids):
                    r=await asyncio.wait_for(api.delete('/tareas/'+id),5)
                    assert r.json()['estado']=='cancelada',r.text
                await terminar()
                id=await enviar(dict(largo,timeout=.03)); await terminar()
                assert main.app.state.tareas[id]['estado']=='fallida'
                id=await enviar(pedido); await terminar()
                assert main.app.state.tareas[id]['resultado']['hash']==esperado

                activas=0; pico=0
                async def descarga(cliente,url):
                    nonlocal activas,pico
                    activas+=1; pico=max(pico,activas)
                    try:
                        await asyncio.sleep(.1)
                        return {'url':url}
                    finally: activas-=1
                with patch.object(main,'descargar_url',descarga):
                    ids=[await enviar({'tipo':'descargar','urls':['http://test/']*4}) for _ in range(2)]
                    await terminar()
                    assert pico==2 and activas==0
                    id=await enviar({'tipo':'descargar','urls':['http://test/']*4})
                    assert (await api.delete('/tareas/'+id)).json()['estado']=='cancelada'
                    assert activas==0
                    id=await enviar({'tipo':'descargar','urls':['http://test/']*4,'timeout':.02})
                    await terminar()
                    assert main.app.state.tareas[id]['estado']=='fallida'
                    assert activas==0
                # El cierre debe parar también procesos ocupados.
                await enviar(largo)
        assert not multiprocessing.active_children()
        assert len(asyncio.all_tasks())==1
        from pathlib import Path
        assert list(Path(tmp).glob('estado_*.json'))
    print('OK: cola llena sin registros huérfanos, hashes, cancelación activa/en executor, timeout, reutilización, semáforo global y cierre sin procesos hijos.')


if __name__=='__main__':
    asyncio.run(verificar())
