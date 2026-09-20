"""Back-compat shim: the real command-building logic now lives in
``codex_reset_watch.scheduler`` (config-driven — see there for the timing rules).
Kept as a separate importable module because `install.py`/`uninstall.py` and
their tests import ``scripts.schtasks`` directly.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from codex_reset_watch.scheduler import (  # noqa: E402,F401
    DAILY_TASK_NAME,
    MONITOR_TASK_NAME,
    daily_task_command,
    delete_task_command,
    monitor_task_command,
)
