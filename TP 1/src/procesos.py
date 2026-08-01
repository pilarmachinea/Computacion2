import signal


def restaurar_senales_hijo():
    """
    Los procesos hijos creados con fork() heredan los handlers de señal
    que el proceso padre tenía registrados en ese momento -- incluido el
    handler personalizado de ManejadorSenales (self-pipe), que NO termina
    el proceso ante SIGTERM, solo escribe al pipe y sigue.

    Esto rompe p.terminate() (que envía SIGTERM): el hijo "atrapa" la señal
    con el handler heredado en vez de terminar, y p.join() queda esperando
    para siempre.

    Se llama al principio de cada función de loop de un proceso hijo
    (recolector_loop, analizador_*_loop) para restaurar el comportamiento
    esperado antes de entrar al while True.
    """
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    signal.signal(signal.SIGUSR2, signal.SIG_IGN)