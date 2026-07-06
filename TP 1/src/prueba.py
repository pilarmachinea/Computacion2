from recolector import recolector_loop
import threading
import time

snapshot = {}
hilo = threading.Thread(target=recolector_loop, args=(snapshot, 2), daemon=True)
hilo.start()

time.sleep(5)
print(f"PIDs en snapshot: {len(snapshot)}")
print(list(snapshot.items())[:2])
import os
print(snapshot.get(os.getpid()))