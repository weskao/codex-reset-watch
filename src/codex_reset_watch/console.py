"""Windows-safe console presentation; shared payloads stay unchanged."""
from __future__ import annotations

import builtins
import sys
import unicodedata
from typing import Any, IO


# UTF-8 support does not imply font support. Use these on every Windows console.
_SIGNS = {
    "✅": "[OK]", "✓": "+", "❌": "[ERROR]", "✗": "x",
    "⚠": "[WARN]", "ℹ": "[INFO]", "🔎": "[CHECK]",
    "🩺": "[DOCTOR]", "🛰": "[CHECKED]", "🕒": "[TIME]",
    "🏷": "[TYPE]", "📝": "[NOTE]", "🔗": "[LINK]",
    "📈": "[AVG]", "🌙": "[WAIT]", "🌐": "[WEB]",
    "🖥": "[PC]", "💻": "[PC]", "📱": "[PHONE]",
    "📁": "[LOGS]", "💾": "[STATE]", "🔮": "[UPCOMING]",
    "🚦": "[STATUS]", "⏳": "[REMAINING]", "🎯": "[CHANCE]",
    "📊": "[CONFIDENCE]", "🪟": "[WINDOW]", "💬": "[SIGNAL]",
    "🚨": "[ALERT]", "🎉": "[NEW]", "🗑": "[REMOVE]",
    "◆": "*", "▍": "|", "▸": ">", "❱": ">", "▏": "|",
    "▴": "^", "▾": "v", "⇥": ">", "⏎": "<",
    "←": "<", "→": ">", "↑": "^", "↓": "v", "↳": ">",
    "·": ".", "•": "*", "…": "...", "–": "-", "—": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "─": "-", "━": "=", "═": "=", "│": "|", "║": "|",
    "█": "#", "▀": "#", "▄": "#", "░": " ",
    "\ufe0e": "", "\ufe0f": "", "\u200d": "",
}


def terminal_text(text: str) -> str:
    """Replace decorations on Windows; preserve original text elsewhere."""
    if sys.platform != "win32":
        return text
    result = []
    for char in text:
        if char in _SIGNS:
            result.append(_SIGNS[char])
        elif 0x2500 <= ord(char) <= 0x257f:
            result.append("+")  # remaining box-drawing corners/junctions
        elif unicodedata.category(char) in ("So", "Co"):
            result.append("*")  # unknown emoji and private font glyphs
        else:
            result.append(char)
    return "".join(result)


def printable_text(text: str, stream: IO[str]) -> str:
    """Also handle Windows streams with legacy code pages without crashing."""
    text = terminal_text(text)
    encoding = getattr(stream, "encoding", None)
    if sys.platform == "win32" and encoding:
        text = text.encode(encoding, errors="backslashreplace").decode(encoding)
    return text


def print_console(*values: Any, **kwargs: Any) -> None:
    """Drop-in print for human-facing output; never use for serialized data."""
    stream = kwargs.get("file") or sys.stdout
    builtins.print(*(printable_text(str(value), stream) for value in values), **kwargs)
