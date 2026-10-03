"""Shared machine labels for automation notifications."""

import functools
import hmac
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import uuid

if __package__:
    from .console import print_console as print
else:  # This module also supports direct invocation from shell scripts.
    from console import print_console as print


# SMBIOS / Win32_SystemEnclosure.ChassisTypes
#
# 8  = Portable
# 9  = Laptop
# 10 = Notebook
# 11 = Hand Held
# 14 = Sub Notebook
# 30 = Tablet
# 31 = Convertible
# 32 = Detachable
_LAPTOP_CHASSIS_TYPES = {
    8, 9, 10, 11, 14, 30, 31, 32,
}

# Known desktop/server-ish chassis types.
_DESKTOP_CHASSIS_TYPES = {
    3,   # Desktop
    4,   # Low Profile Desktop
    5,   # Pizza Box
    6,   # Mini Tower
    7,   # Tower
    13,  # All in One
    15,  # Space-Saving
    16,  # Lunch Box
    17,  # Main System Chassis
    23,  # Rack Mount Chassis
    35,  # Mini PC
    36,  # Stick PC
}


# These are FALLBACK hints only.
# Hardware chassis information always takes priority.
_LAPTOP_NAME_PATTERNS = (
    # Generic
    r"\blaptop\b",
    r"\bnotebook\b",
    r"\bultrabook\b",
    r"\bportable\b",

    # Apple
    r"\bmacbook\b",
    r"\bmacbook air\b",
    r"\bmacbook pro\b",

    # ASUS
    r"\bvivobook\b",
    r"\bzenbook\b",
    r"\bexpertbook\b",
    r"\bproart p[xp]\b",
    r"\brog zephyrus\b",
    r"\brog flow\b",
    r"\btuf gaming [af]\d{2}\b",

    # Lenovo
    r"\bthinkpad\b",
    r"\bideapad\b",
    r"\byoga\b",
    r"\bloq (?:14|15|16|17)\b",
    r"\blegion (?:slim|pro|\d)\b",

    # Microsoft
    r"\bsurface laptop\b",
    r"\bsurface book\b",
    r"\bsurface pro\b",

    # Dell
    r"\blatitude\b",
    r"\bxps (?:13|14|15|16|17)\b",

    # HP
    r"\belitebook\b",
    r"\bprobook\b",
    r"\bzbook\b",
    r"\bdragonfly\b",
    r"\bspectre x360\b",
    r"\bpavilion .*laptop\b",
    r"\benvy .*laptop\b",
    r"\bomen .*laptop\b",
    r"\bvictus .*laptop\b",

    # Acer
    r"\bswift\b",
    r"\btravelmate\b",
    r"\bchromebook\b",

    # Huawei / Honor
    r"\bmatebook\b",
    r"\bmagicbook\b",

    # LG
    r"\blg gram\b",

    # Razer
    r"\brazer blade\b",

    # Framework
    r"\bframework laptop\b",

    # Sony / modern VAIO
    r"\bvaio\b",

    # Samsung
    r"\bgalaxy book\b",

    # Xiaomi / Redmi
    r"\bredmibook\b",
    r"\bmi notebook\b",
)


@functools.cache
def host_name() -> str:
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/usr/sbin/scutil", "--get", "ComputerName"],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            stdout = (result.stdout or "").strip()
            if result.returncode == 0 and stdout:
                return stdout
        except (OSError, subprocess.TimeoutExpired):
            pass

    # Cross-platform fallback.
    return (socket.gethostname() or "unknown-host").removesuffix(".local")


def _matches_laptop_name(value: str) -> bool:
    """Weak fallback only: infer portable hardware from a model/host string."""
    value = (value or "").lower()

    return any(
        re.search(pattern, value, flags=re.IGNORECASE)
        for pattern in _LAPTOP_NAME_PATTERNS
    )


@functools.cache
def _mac_machine_kind() -> str:
    """
    Return laptop / desktop / unknown.

    Internal AppleSmartBattery is the strongest practical signal for a
    Mac notebook and works for modern MacBook Air/Pro models whose model
    identifiers may simply be "Mac14,x", "Mac15,x", etc.
    """
    try:
        result = subprocess.run(
            [
                "/usr/sbin/ioreg",
                "-r",
                "-c",
                "AppleSmartBattery",
                "-d",
                "1",
            ],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )

        if "AppleSmartBattery" in (result.stdout or ""):
            return "laptop"
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Fallback to System Profiler model name.
    try:
        result = subprocess.run(
            [
                "/usr/sbin/system_profiler",
                "SPHardwareDataType",
            ],
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )

        stdout = result.stdout or ""

        match = re.search(
            r"^\s*Model Name:\s*(.+?)\s*$",
            stdout,
            flags=re.MULTILINE,
        )

        if match:
            model_name = match.group(1).strip()

            if _matches_laptop_name(model_name):
                return "laptop"

            lower = model_name.lower()

            if any(
                desktop_name in lower
                for desktop_name in (
                    "mac mini",
                    "mac studio",
                    "mac pro",
                    "imac",
                )
            ):
                return "desktop"

    except (OSError, subprocess.TimeoutExpired):
        pass

    return "unknown"


@functools.cache
def _windows_hardware_info() -> dict:
    """
    Read Windows SMBIOS information through CIM.

    Example:
        {
            "Manufacturer": "ASUSTeK COMPUTER INC.",
            "Model": "Vivobook_ASUSLaptop K3605VC_K3605VC",
            "PCSystemType": 2,
            "ChassisTypes": [10]
        }
    """
    script = r"""
$ErrorActionPreference = 'Stop'

$computer = Get-CimInstance Win32_ComputerSystem
$enclosure = Get-CimInstance Win32_SystemEnclosure |
    Select-Object -First 1

[PSCustomObject]@{
    Manufacturer = [string]$computer.Manufacturer
    Model         = [string]$computer.Model
    PCSystemType  = [int]$computer.PCSystemType
    ChassisTypes  = @($enclosure.ChassisTypes)
} | ConvertTo-Json -Compress
""".strip()

    # Windows PowerShell is normally available on Windows 10/11.
    # pwsh.exe is included as a fallback for PowerShell 7-only setups.
    for executable in ("powershell.exe", "pwsh.exe"):
        try:
            result = subprocess.run(
                [
                    executable,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
                text=True,
                capture_output=True,
                timeout=8,
                check=False,
            )

            stdout = (result.stdout or "").strip()

            if result.returncode == 0 and stdout:
                value = json.loads(stdout)

                if isinstance(value, dict):
                    return value

        except (
            OSError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ):
            continue

    return {}


@functools.cache
def _windows_machine_kind() -> str:
    """
    Prefer Windows chassis metadata over model-name heuristics.

    PCSystemType:
        1 = Desktop
        2 = Mobile
        3 = Workstation
        4+ = Server / appliance variants
    """
    info = _windows_hardware_info()

    chassis = info.get("ChassisTypes") or []

    if not isinstance(chassis, list):
        chassis = [chassis]

    chassis_types = set()

    for value in chassis:
        try:
            chassis_types.add(int(value))
        except (TypeError, ValueError):
            pass

    # Chassis has highest priority.
    if chassis_types & _LAPTOP_CHASSIS_TYPES:
        return "laptop"

    if chassis_types & _DESKTOP_CHASSIS_TYPES:
        return "desktop"

    # PCSystemType is the next strongest Windows hint.
    try:
        pc_system_type = int(info.get("PCSystemType") or 0)
    except (TypeError, ValueError):
        pc_system_type = 0

    if pc_system_type == 2:
        return "laptop"

    if pc_system_type in {
        1,  # Desktop
        3,  # Workstation
        4,  # Enterprise Server
        5,  # SOHO Server
        6,  # Appliance PC
        7,  # Performance Server
        8,  # Maximum
    }:
        return "desktop"

    # Last-resort model/manufacturer detection.
    manufacturer = str(info.get("Manufacturer") or "")
    model = str(info.get("Model") or "")

    if _matches_laptop_name(f"{manufacturer} {model}"):
        return "laptop"

    return "unknown"


@functools.cache
def _linux_machine_kind() -> str:
    """
    Best-effort SMBIOS detection for Linux.

    This may also work for some WSL environments if the host DMI data is
    exposed, but WSL should not be assumed to expose accurate chassis info.
    """
    try:
        value = Path("/sys/class/dmi/id/chassis_type").read_text(
            encoding="ascii"
        ).strip()

        chassis_type = int(value)

        if chassis_type in _LAPTOP_CHASSIS_TYPES:
            return "laptop"

        if chassis_type in _DESKTOP_CHASSIS_TYPES:
            return "desktop"

    except (OSError, UnicodeError, ValueError):
        pass

    # Fallback to DMI product names.
    values = []

    for filename in (
        "/sys/class/dmi/id/sys_vendor",
        "/sys/class/dmi/id/product_name",
        "/sys/class/dmi/id/product_version",
    ):
        try:
            value = Path(filename).read_text(
                encoding="utf-8",
                errors="ignore",
            ).strip()

            if value:
                values.append(value)

        except OSError:
            pass

    if _matches_laptop_name(" ".join(values)):
        return "laptop"

    return "unknown"


def machine_kind(name: str = "") -> str:
    """
    Return:
        laptop
        desktop
        unknown

    Unknown intentionally remains unknown instead of being guessed.
    """
    if sys.platform == "darwin":
        kind = _mac_machine_kind()

    elif sys.platform == "win32":
        kind = _windows_machine_kind()

    elif sys.platform.startswith("linux"):
        kind = _linux_machine_kind()

    else:
        kind = "unknown"

    if kind != "unknown":
        return kind

    # Very last fallback: user-defined hostname.
    if _matches_laptop_name(name):
        return "laptop"

    return "unknown"


def host_emoji(name: str) -> str:
    """
    Laptop / portable:
        💻

    Desktop OR anything that cannot be confidently identified:
        🖥️
    """
    return "💻" if machine_kind(name) == "laptop" else "🖥️"


def device_code() -> str:
    """macOS serial, or an app-specific OS identity digest; empty if unavailable."""
    try:
        if sys.platform == "darwin":
            result = subprocess.run(
                [
                    "/usr/sbin/ioreg",
                    "-rd1",
                    "-c",
                    "IOPlatformExpertDevice",
                ],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )

            match = re.search(
                r'"IOPlatformSerialNumber"\s*=\s*"([A-Za-z0-9]{5,})"',
                result.stdout or "",
            )

            return (
                match.group(1)
                if result.returncode == 0 and match
                else ""
            )

        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                value, _ = winreg.QueryValueEx(
                    key,
                    "MachineGuid",
                )

            return _identity_digest(value)

        if sys.platform.startswith("linux"):
            for filename in (
                "/etc/machine-id",
                "/var/lib/dbus/machine-id",
            ):
                try:
                    code = _identity_digest(
                        Path(filename)
                        .read_text(encoding="ascii")
                        .strip()
                    )

                    if code:
                        return code

                except (OSError, UnicodeError):
                    continue

    except (
        OSError,
        subprocess.TimeoutExpired,
        UnicodeError,
        ImportError,
    ):
        pass

    return ""


def _identity_digest(value: str) -> str:
    """Never expose the raw OS installation ID in notification labels."""
    try:
        identity = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return ""

    if identity.int == 0:
        return ""

    return hmac.new(
        identity.bytes,
        b"automation-notification-host",
        "sha256",
    ).hexdigest()[:12]


def masked_device_code() -> str:
    """Show only four characters; unavailable identities are explicitly unknown."""
    code = device_code()

    return (
        f"{code[:4]}{'*' * (len(code) - 4)}"
        if code
        else "unknown"
    )


def device_label() -> str:
    """
    Full display label for notifications.

    Examples:
        💻 MacBook Pro · C02X********
        💻 ASUS-Vivobook · a1b2********
        💻 ThinkPad-T14 · 8cef********
        🖥️ Mac mini · FVFG********
        🖥️ DESKTOP-ABC123 · 4e12********

    Unknown machine types intentionally use 🖥️.
    """
    name = host_name()

    return f"{host_emoji(name)} {name} · {masked_device_code()}"


def launchd_footer(label: str, *paths) -> str:
    """Footer naming the launchd job and its logs."""
    groups: dict = {}

    for path in map(Path, filter(None, paths)):
        names = groups.setdefault(path.parent, [])

        if path.name not in names:
            names.append(path.name)

    lines = [f"launchd: {label}"]

    for directory, names in groups.items():
        lines += (
            [f"log: {directory / names[0]}"]
            if len(names) == 1
            else [
                f"logs: {directory}/",
                " · ".join(names),
            ]
        )

    return "".join(f"\n\n{line}" for line in lines)


if __name__ == "__main__":
    # CLI entry point for shell scripts.
    if "--name" in sys.argv[1:]:
        print(host_name())

    elif "--kind" in sys.argv[1:]:
        print(machine_kind(host_name()))

    elif "--emoji" in sys.argv[1:]:
        print(host_emoji(host_name()))

    else:
        print(device_label())
