# SIGINT/SIGTERM delivered while the main thread holds _LIVE_LOCK must not deadlock the handler
# (cancel_everything takes the same lock). Run in a subprocess with a hard timeout: a deadlock is a timeout, not a hang.
from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DRIVER = """
import os, signal, sys
sys.path.insert(0, sys.argv[1])
from proving_ground import runner
runner.install_cancellation()
try:
    with runner._LIVE_LOCK:
        os.kill(os.getpid(), int(sys.argv[2]))
        for _ in range(10_000_000):
            pass
    print("NOT_INTERRUPTED")
except KeyboardInterrupt:
    print("INTERRUPTED", runner.CANCEL.is_set())
"""


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_signal_while_the_live_lock_is_held_cancels_without_deadlock(sig) -> None:
    out = subprocess.run([sys.executable, "-B", "-c", DRIVER, str(ROOT), str(int(sig))], capture_output=True,
                         text=True, timeout=30, check=False)
    assert out.stdout.strip() == "INTERRUPTED True", out.stderr[-2000:]
