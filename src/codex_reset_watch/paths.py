"""Cross-platform default config/state/log directories.

`platform`/`home`/`environ` are override hooks for tests only — real callers omit
them and get `sys.platform` plus a concrete, usable `pathlib.Path`.
"""
from __future__ import annotations

import os
import pathlib
import json
import subprocess
import sys
import uuid
from typing import Any, Dict, Optional, Type

APP_DIR_NAME = "codex-reset-watch"


def write_private(target: pathlib.Path, content: str) -> None:
    """Atomically write owner-only text; fail before writing if protection fails."""
    target = pathlib.Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise OSError("Refusing to overwrite a symlink")
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    created = False
    try:
        if os.name == "nt":
            # The ACL is attached at creation, before content is written.
            # The path and content travel on stdin, never the command line.
            script = (
                "$ErrorActionPreference='Stop'; "
                "$data=[Console]::In.ReadToEnd() | ConvertFrom-Json; "
                "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User; "
                "$acl=[Security.AccessControl.FileSecurity]::new(); "
                "$acl.SetAccessRuleProtection($true,$false); $acl.SetOwner($sid); "
                "$rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,"
                "[Security.AccessControl.FileSystemRights]::FullControl,"
                "[Security.AccessControl.AccessControlType]::Allow); "
                "$acl.AddAccessRule($rule); "
                "$file=[IO.FileStream]::new($data.path,[IO.FileMode]::CreateNew,"
                "[Security.AccessControl.FileSystemRights]::FullControl,[IO.FileShare]::None,4096,"
                "[IO.FileOptions]::None,$acl); "
                "$writer=[IO.StreamWriter]::new($file,[Text.UTF8Encoding]::new($false)); "
                "try { $writer.Write($data.content) } finally { $writer.Dispose() }"
            )
            created = True  # PowerShell may create it before failing or timing out
            try:
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                    input=json.dumps({"path": str(temporary), "content": content}, ensure_ascii=True),
                    capture_output=True, text=True, timeout=15, check=False,
                )
            except subprocess.SubprocessError as exc:
                raise OSError("Unable to create an owner-only file") from exc
            if result.returncode:
                raise OSError("Unable to create an owner-only file")
        else:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
        os.replace(temporary, target)
        created = False
    finally:
        if created:
            temporary.unlink(missing_ok=True)


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
