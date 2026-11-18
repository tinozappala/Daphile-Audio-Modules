"""Demand-driven shared snapshot cache; no periodic work without viewers."""
import copy
import threading
import time

class SnapshotCache:
    def __init__(self,provider,operations):
        self.provider=provider;self.operations=operations;self.lock=threading.Lock()
        self.value=None;self.updated=0;self.running=False;self.error=None

    def seed(self,value):
        with self.lock:
            self.value=copy.deepcopy(value);self.updated=time.monotonic();self.error=None

    def invalidate(self):
        with self.lock:self.updated=0

    def get(self):
        with self.lock:
            age=time.monotonic()-self.updated
            if not self.running and age>=15:
                self.running=True;threading.Thread(target=self.refresh,daemon=True).start()
            return {'state':copy.deepcopy(self.value),'refreshing':self.running,
                    'age_seconds':round(age,2) if self.value else None,'error':self.error}

    def refresh(self):
        try:
            # Do not queue background reads ahead of an explicit transaction.
            if not self.operations.acquire(blocking=False):return
            try:self.seed(self.provider())
            finally:self.operations.release()
        except Exception:
            with self.lock:self.error='Lettura automatica non disponibile. Puoi rileggere il DAC.'
        finally:
            with self.lock:self.running=False
