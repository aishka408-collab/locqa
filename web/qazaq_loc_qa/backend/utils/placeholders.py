import re
from typing import List

_ARGUMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")
_ICU_STYLE = re.compile(r"\s*(plural|select|selectordinal)\s*,")
_SELECTOR = re.compile(r"(?:=[+-]?\d+|[A-Za-z_][A-Za-z0-9_-]*)")
_NON_BRACE_PLACEHOLDER = re.compile(r"%(?:\d+\$)?[sdif]|%\{[^}]+\}|<[^>]+>")


def extract_placeholders(text: str) -> List[str]:
    """Extract interpolation tokens, including ICU arguments and nested branches."""
    placeholders = [match.group(0) for match in _NON_BRACE_PLACEHOLDER.finditer(text or "")]
    value = text or ""

    def skip_braced_expression(start: int) -> int:
        depth = 0
        for index in range(start, len(value)):
            if value[index] == "{":
                depth += 1
            elif value[index] == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
        return len(value)

    def scan_message(start: int, stop_at_closing_brace: bool = False,
                     plural_depth: int = 0) -> int:
        index = start
        while index < len(value):
            char = value[index]
            if char == "}" and stop_at_closing_brace:
                return index + 1
            if char == "#":
                if plural_depth:
                    placeholders.append("#")
                index += 1
                continue
            if char != "{":
                index += 1
                continue

            name_match = _ARGUMENT_NAME.match(value, index + 1)
            if not name_match:
                index += 1
                continue

            name = name_match.group(0)
            delimiter = name_match.end()
            while delimiter < len(value) and value[delimiter].isspace():
                delimiter += 1

            if delimiter < len(value) and value[delimiter] == "}":
                placeholders.append("{" + name + "}")
                index = delimiter + 1
                continue

            if delimiter >= len(value) or value[delimiter] != ",":
                index += 1
                continue

            placeholders.append("{" + name + "}")
            style = _ICU_STYLE.match(value, delimiter + 1)
            if not style:
                index = skip_braced_expression(index)
                continue

            branch_index = style.end()
            inner_plural_depth = plural_depth + (style.group(1) in ("plural", "selectordinal"))
            while branch_index < len(value):
                while branch_index < len(value) and value[branch_index].isspace():
                    branch_index += 1
                if branch_index >= len(value) or value[branch_index] == "}":
                    break

                selector = _SELECTOR.match(value, branch_index)
                if selector:
                    body_start = selector.end()
                    while body_start < len(value) and value[body_start].isspace():
                        body_start += 1
                    if body_start < len(value) and value[body_start] == "{":
                        branch_index = scan_message(
                            body_start + 1,
                            stop_at_closing_brace=True,
                            plural_depth=inner_plural_depth,
                        )
                        continue
                branch_index += 1

            index = branch_index + 1 if branch_index < len(value) and value[branch_index] == "}" else branch_index
        return index

    scan_message(0)
    return placeholders
