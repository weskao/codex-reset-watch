"""Cross-platform default config/state/log directories.

`platform`/`home`/`environ` are override hooks for tests only — real callers omit
them and get `sys.platform` plus a concrete, usable `pathlib.Path`.
"""
from __future__ import annotations

import os
import pathlib
import sys
from typing import Any, Dict, Optional, Type

from telegram_kit import write_private  # noqa: F401 - re-exported

APP_DIR_NAME = "codex-reset-watch"


def _path_cls(platform: str) -> Type[pathlib.PurePath]:
    if platform == sys.platform:
        return pathlib.Path
    return pathlib.PureWindowsPath if platform == "win32" else pathlib.PurePosixPath


def _root(platform: str, home: Any, environ: Dict[str, str], env_var: str, *fallback_parts: str) -> pathlib.PurePath:
    cls = _path_cls(platform)
    value = environ.get(env_var)
    if value:
        return cls(value)
    return cls(home).joinpath(*fallback_parts)


def _resolve(
    platform: Optional[str],
    home: Any,
    environ: Optional[Dict[str, str]],
    darwin_parts: tuple,
    win_env_var: str,
    win_fallback_parts: tuple,
    xdg_env_var: str,
    xdg_fallback_parts: tuple,
    *extra_parts: str,
) -> pathlib.PurePath:
    platform = platform or sys.platform
    home = home if home is not None else pathlib.Path.home()
    environ = environ if environ is not None else os.environ
    cls = _path_cls(platform)
    if platform == "darwin":
        base = cls(home).joinpath(*darwin_parts)
    elif platform == "win32":
        base = _root(platform, home, environ, win_env_var, *win_fallback_parts)
    else:
        base = _root(platform, home, environ, xdg_env_var, *xdg_fallback_parts)
    return base.joinpath(APP_DIR_NAME, *extra_parts)


def app_config_dir(*, platform: Optional[str] = None, home: Any = None, environ: Optional[Dict[str, str]] = None) -> pathlib.PurePath:
    return _resolve(
        platform, home, environ,
        ("Library", "Application Support"),
        "APPDATA", ("AppData", "Roaming"),
        "XDG_CONFIG_HOME", (".config",),
    )


def app_state_dir(*, platform: Optional[str] = None, home: Any = None, environ: Optional[Dict[str, str]] = None) -> pathlib.PurePath:
    return _resolve(
        platform, home, environ,
        ("Library", "Application Support"),
        "LOCALAPPDATA", ("AppData", "Local"),
        "XDG_STATE_HOME", (".local", "state"),
    )


def app_log_dir(*, platform: Optional[str] = None, home: Any = None, environ: Optional[Dict[str, str]] = None) -> pathlib.PurePath:
    platform = platform or sys.platform
    if platform == "darwin":
        return _resolve(platform, home, environ, ("Library", "Logs"), "", (), "", ())
    if platform == "win32":
        return _resolve(platform, home, environ, (), "LOCALAPPDATA", ("AppData", "Local"), "", (), "Logs")
    return _resolve(platform, home, environ, (), "", (), "XDG_STATE_HOME", (".local", "state"), "log")
