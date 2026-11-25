import multiprocessing
from concurrent.futures import Future, wait
from threading import Lock, Thread, local
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


class CalculationTimeout(Exception):
    pass


def exchange(pipe, message, reply):
    try:
        pipe.send(message)
        reply.set_result(pipe.recv())
    except Exception as error:
        reply.set_exception(error)


class CalculationPool:
    def __init__(self, timeout):
        self.timeout = timeout
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
        if child.is_alive():
            child.terminate()
            child.join(timeout=0.2)
        if child.is_alive():
            child.kill()
            child.join(timeout=1)
        pipe.close()
        if not child.is_alive():
            with self.lock:
                self.channels.pop(child.pid, None)
            child.close()
        self.local.pair = None

    def calculate(self, handler, inputs, delay, renew):
        pipe, child = self.channel()
        deadline = monotonic() + delay + self.timeout
        reply = Future()
        response = None
        try:
            Thread(target=exchange, args=(pipe, (handler, inputs, delay), reply), daemon=True).start()
            heartbeat = monotonic() + 1
            while not reply.done():
                now = monotonic()
                if now >= deadline:
                    raise CalculationTimeout
                if now >= heartbeat:
                    if not renew():
                        break
                    heartbeat = now + 1
                wait((reply,), timeout=max(0, min(deadline, heartbeat) - monotonic()))
            else:
                response = reply.result()
        except Exception:
            self.discard(pipe, child)
            raise
        if response is None:
            self.discard(pipe, child)
            return None
        ok, output = response
        if not ok:
            raise RuntimeError(output)
        return output

    def close(self):
        for pipe, child in list(self.channels.values()):
            self.discard(pipe, child)
