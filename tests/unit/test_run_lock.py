import subprocess
import sys
from pathlib import Path

import pytest

from gcreelmap.run_lock import RunLock, RunLockHeld


def test_second_lock_raises_while_held(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    with RunLock(path), pytest.raises(RunLockHeld), RunLock(path):
        pass


def test_lock_acquirable_after_release(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    with RunLock(path):
        pass
    with RunLock(path):
        pass  # does not raise


def test_parent_dir_created_if_missing(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "run.lock"
    with RunLock(path):
        pass
    assert path.parent.exists()


def test_subprocess_holding_lock_blocks_parent(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    script = (
        "from pathlib import Path\n"
        "from gcreelmap.run_lock import RunLock\n"
        "import time\n"
        f"lock = RunLock(Path(r'{path}'))\n"
        "lock.__enter__()\n"
        "print('held', flush=True)\n"
        "time.sleep(10)\n"
    )
    proc = subprocess.Popen(  # noqa: S603 - fixed args, sys.executable, no untrusted input
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert line.strip() == "held"
        with pytest.raises(RunLockHeld), RunLock(path):
            pass
    finally:
        proc.kill()
        proc.wait()
