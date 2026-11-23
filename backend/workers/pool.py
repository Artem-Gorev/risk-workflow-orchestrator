import multiprocessing
from threading import Lock, local
from time import monotonic, sleep

from backend.workers.catalog import PROCESSES


def serve(pipe):
    with pipe:
        while True:
            try:
                handler, inputs, delay = pipe.recv()
            except EOFError:
                return
            try:
                sleep(delay)
                result = PROCESSES[handler].calculate(inputs)
                pipe.send((True, result))
            except (ArithmeticError, ValueError, TypeError, KeyError) as exc:
                pipe.send((False, str(exc)))


class CalculationPool:
    def __init__(self):
        self.context = multiprocessing.get_context("spawn")
        self.local = local()
        self.channels = {}
        self.lock = Lock()

    def channel(self):
        pair = getattr(self.local, "pair", None)
        if pair is None:
            pipe, remote = self.context.Pipe()
            child = self.context.Process(target=serve, args=(remote,), daemon=True)
            try:
                child.start()
            finally:
                remote.close()
            pair = pipe, child
            with self.lock:
                self.channels[child.pid] = pair
            self.local.pair = pair
        return pair

    def discard(self, pipe, child):
        pipe.close()
        if child.is_alive():
            child.terminate()
        child.join()
        with self.lock:
            self.channels.pop(child.pid, None)
        child.close()
        self.local.pair = None

    def calculate(self, handler, inputs, delay, renew):
        pipe, child = self.channel()
        try:
            pipe.send((handler, inputs, delay))
            heartbeat = monotonic() + 1
            while not pipe.poll(0.2):
                if monotonic() >= heartbeat:
                    if not renew():
                        self.discard(pipe, child)
                        return None
                    heartbeat = monotonic() + 1
            ok, output = pipe.recv()
        except Exception:
            self.discard(pipe, child)
            raise
        if not ok:
            raise RuntimeError(output)
        return output

    def close(self):
        for pipe, child in list(self.channels.values()):
            self.discard(pipe, child)
