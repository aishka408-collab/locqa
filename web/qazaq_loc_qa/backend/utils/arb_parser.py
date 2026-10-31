import re
import json
from collections import OrderedDict
from pathlib import Path
from typing import List, Union, Optional

# Regular expressions for ARB lines
_KV_RE = re.compile(r'^\s*"(?P<key>(?!@@)[^"]*)"\s*:\s*"(?P<value>(?:\\"|[^\"])*?)"\s*,?\s*$')
_COMMENT_RE = re.compile(r'^\s*"(?P<key>@@[^\"]*)"\s*:\s*"(?P<value>(?:\\"|[^"])*?)"\s*,?\s*$')


def decode_arb_string(raw: str) -> str:
    """Decode JSON/ARB escapes (``\\n``, ``\\"``, ``\\uXXXX`` ...) in *raw*.

    The ARB format stores strings using JSON escaping, therefore the textual
    value on disk is *not* the value an analyzer should inspect.  For example
    a literal ``\\n`` would otherwise be seen as a Latin letter ``n`` and a
    ``\\uXXXX`` sequence would leak hex digits into the text.  If the value
    cannot be decoded we conservatively return it unchanged.
    """
    try:
        return json.loads(f'"{raw}"')
    except (ValueError, TypeError):
        return raw


def encode_arb_string(value: str) -> str:
    """Encode *value* the same way JSON/ARB encodes strings (without quotes)."""
    return json.dumps(value, ensure_ascii=False)[1:-1]


class ARBEntry:
    """Represents a single line entry in an ARB file.

    * type – one of "kv", "comment", "other" (braces, commas, etc.)
    * key   – None for "other" entries
    * value – None for "other" entries
    * raw   – original line including newline characters
    """

    __slots__ = ("type", "key", "value", "raw", "modified")

    def __init__(self, entry_type: str, key: Optional[str], value: Optional[str], raw: str):
        self.type = entry_type
        self.key = key
        self.value = value
        self.raw = raw
        # ``modified`` is set by ARBFile.set; untouched lines are written back
        # byte-for-byte so formatting, indentation and escaping are preserved.
        self.modified = False

    def __repr__(self) -> str:
        return f"ARBEntry(type={self.type!r}, key={self.key!r}, value={self.value!r})"

    def rendered(self, new_value: Optional[str] = None) -> str:
        """Return the line with *new_value* (if provided) formatted the same way as the original.
        """
        if self.type == "kv":
            if new_value is None and not self.modified:
                # Untouched entries keep their original byte representation.
                return self.raw
            val = new_value if new_value is not None else self.value
            escaped_key = encode_arb_string(self.key or "")
            escaped = encode_arb_string(val if val is not None else "")
            # Preserve possible trailing comma from the original line
            suffix = "," if self.raw.rstrip().endswith(",") else ""
            return f'    "{escaped_key}": "{escaped}"{suffix}\n'
        if self.type == "comment":
            # Comments are never edited by the analyzers; keep them verbatim.
            return self.raw
        # For everything else (braces, commas, blank lines) keep original
        return self.raw


class ARBFile:
    """High‑level wrapper for an ARB file that keeps ordering, comments and allows safe edits.

    Example usage::

        arb = ARBFile.load("intl_kk.arb")
        print(arb.get("skip"))
        arb.set("skip", "Жаңадан")
        arb.dump("intl_kk_fixed.arb")
    """

    def __init__(self, entries: List[ARBEntry]):
        self.entries: List[ARBEntry] = entries
        # Build an ordered dict of key‑value pairs for quick look‑up while *preserving* order
        self.kv_dict: OrderedDict[str, str] = OrderedDict()
        for e in entries:
            if e.type == "kv":
                self.kv_dict[e.key] = e.value

    # ---------------------------------------------------------------------
    #   Factory & I/O helpers
    # ---------------------------------------------------------------------
    @classmethod
    def load(cls, path: Union[str, Path]) -> "ARBFile":
        """Parse *path* and return an :class:`ARBFile` instance.

        The parser works line by line to preserve ordering and comments. Valid
        compact JSON is normalized only when no key/value lines are found.
        """
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(str(p))
        with p.open("r", encoding="utf-8", newline="\n") as f:
            content = f.read()
        entries = cls._parse_lines(content.splitlines(keepends=True))
        if not any(entry.type == "kv" for entry in entries):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                normalized = json.dumps(parsed, ensure_ascii=False, indent=2)
                if content.endswith(("\n", "\r")):
                    normalized += "\n"
                entries = cls._parse_lines(normalized.splitlines(keepends=True))
        return cls(entries)

    @staticmethod
    def _parse_lines(lines: List[str]) -> List[ARBEntry]:
        entries: List[ARBEntry] = []
        depth = 0
        for line in lines:
            stripped = line.strip()
            line_depth = depth
            in_string = False
            escaped = False
            for char in line:
                if escaped:
                    escaped = False
                elif char == "\\" and in_string:
                    escaped = True
                elif char == '"':
                    in_string = not in_string
                elif not in_string:
                    if char in "{[":
                        depth += 1
                    elif char in "}]":
                        depth -= 1
            if not stripped:
                entries.append(ARBEntry("other", None, None, line))
                continue
            if stripped in ('{', '}'):
                entries.append(ARBEntry("other", None, None, line))
                continue
            if line_depth != 1:
                entries.append(ARBEntry("other", None, None, line))
                continue
            m_comment = _COMMENT_RE.match(line)
            if m_comment:
                entries.append(ARBEntry(
                    "comment",
                    decode_arb_string(m_comment.group("key")),
                    decode_arb_string(m_comment.group("value")),
                    line,
                ))
                continue
            m_kv = _KV_RE.match(line)
            if m_kv:
                key = decode_arb_string(m_kv.group("key"))
                if key.startswith("@#"):
                    entries.append(ARBEntry(
                        "comment",
                        key,
                        decode_arb_string(m_kv.group("value")),
                        line,
                    ))
                else:
                    entries.append(ARBEntry(
                        "kv",
                        key,
                        decode_arb_string(m_kv.group("value")),
                        line,
                    ))
                continue
            entries.append(ARBEntry("other", None, None, line))
        return entries

    # ---------------------------------------------------------------------
    #   Public API for reading / mutating values
    # ---------------------------------------------------------------------
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.kv_dict.get(key, default)

    def set(self, key: str, new_value: str) -> None:
        """Change the value for *key* – if the key does not exist an exception is raised.

        ARB files in the wild occasionally contain duplicate keys; JSON semantics
        make the *last* occurrence effective.  Updating every occurrence keeps
        the effective value and the visible file consistent.
        """
        if key not in self.kv_dict:
            raise KeyError(key)
        # Update the dict for fast look‑up
        self.kv_dict[key] = new_value
        # Also update all corresponding entry objects so that ``dump`` keeps formatting
        for e in self.entries:
            if e.type == "kv" and e.key == key:
                e.value = new_value
                e.modified = True

    # ---------------------------------------------------------------------
    #   Persistence
    # ---------------------------------------------------------------------
    def dump(self, path: Union[str, Path]) -> None:
        """Write the ARB back to *path* preserving original ordering and line endings.
        """
        p = Path(path)
        with p.open("w", encoding="utf-8", newline="\n") as f:
            for e in self.entries:
                f.write(e.rendered())

    # ---------------------------------------------------------------------
    #   Helper utilities
    # ---------------------------------------------------------------------
    @property
    def locale(self) -> Optional[str]:
        """Return the ``@@locale`` metadata value (e.g. ``"kk"``) if present."""
        for e in self.entries:
            if e.type == "comment" and e.key == "@@locale" and e.value:
                return str(e.value).strip().lower()
        return None

    def keys(self) -> List[str]:
        return list(self.kv_dict.keys())

    def items(self):
        return self.kv_dict.items()

    def __len__(self) -> int:
        """Return number of key/value entries (ignore comments, other)."""
        return len([e for e in self.entries if e.type == "kv"])

    def __contains__(self, key: str) -> bool:
        return key in self.kv_dict

    def __repr__(self) -> str:
        return f"ARBFile({len(self)} keys)"

# -------------------------------------------------------------------------
#   Simple command‑line helper (useful for manual debugging)
# -------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser(description="Parse an ARB file and optionally edit a key.")
    parser.add_argument("file", help="Path to the ARB file")
    parser.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="Set a key to a new value and rewrite the file")
    args = parser.parse_args()
    arb = ARBFile.load(args.file)
    print(f"Loaded {len(arb)} key/value pairs (plus comments/metadata).")
    if args.set:
        key, value = args.set
        arb.set(key, value)
        arb.dump(args.file)
        print(f"Updated {key!r} → {value!r}")
