from pathlib import Path

from filelock import FileLock, Timeout


class RunLockHeld(Exception):
    pass


class RunLock:
    """Exclusive, per-OS-process run lock (A9): overlapping scheduled runs cannot both proceed."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = FileLock(str(path), timeout=0)

    def __enter__(self) -> "RunLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock.acquire()
        except Timeout as exc:
            raise RunLockHeld(f"run lock held: {self._path}") from exc
        return self

    def __exit__(self, *exc: object) -> None:
        self._lock.release()
