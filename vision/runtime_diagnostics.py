"""Record lightweight diagnostics for the Raspberry Pi game runner."""

from contextlib import contextmanager
import faulthandler
import logging
from pathlib import Path
from threading import Event, Thread
import time


logger = logging.getLogger("chro.diagnostics")


@contextmanager
def record_runtime(path: Path, camera_index: int):
    links = Path("/dev/v4l/by-id")
    stopped = Event()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    level, propagate = logger.level, logger.propagate

    def monitor():
        previous = None
        next_sample = 0.0
        while not stopped.is_set():
            try:
                devices = tuple((link.name, str(link.resolve()))
                                for link in sorted(links.glob("*video-index0")) if link.exists())
                if devices != previous or time.monotonic() >= next_sample:
                    load = Path("/proc/loadavg").read_text().strip()
                    memory = [line for line in Path("/proc/meminfo").read_text().splitlines()
                              if line.startswith(("MemAvailable:", "SwapFree:"))]
                    logger.info("System load=%s memory=%s configured=/dev/video%s USB=%s",
                                load, "; ".join(memory), camera_index, devices)
                    previous = devices
                    next_sample = time.monotonic() + 15
            except OSError as exc:
                logger.warning("System sample failed: %s", exc)
            stopped.wait(2)

    worker = Thread(target=monitor, name="runtime-diagnostics", daemon=True)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        with path.open("ab", buffering=0) as faults:
            already_enabled = faulthandler.is_enabled()
            if not already_enabled:
                faulthandler.enable(file=faults, all_threads=True)
            try:
                logger.info("Game diagnostics started")
                worker.start()
                yield
            finally:
                stopped.set()
                if worker.ident is not None:
                    worker.join()
                logger.info("Game diagnostics closed")
                if not already_enabled:
                    faulthandler.disable()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate
        handler.close()
