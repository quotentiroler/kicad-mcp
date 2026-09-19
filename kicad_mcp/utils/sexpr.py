"""S-expression reader for KiCad files.

KiCad writes parens inside quoted strings as a matter of course: net names
for unconnected pads look like "unconnected-(U1-Pad3)", descriptions carry
whole sentences, and 3D model paths on Windows end in an escaped
backslash. Anything that counts brackets in the raw text has to know where
the strings are, or it silently reads the wrong extent.
"""

ESCAPE = chr(92)
WHITESPACE = " \t\n\r"


def _read(text: str) -> list:
    stack: list[list] = [[]]
    token = ""
    in_string = False
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if in_string:
            token += ch
            if ch == ESCAPE and i + 1 < n:
                token += text[i + 1]
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            token += ch
        elif ch == "(":
            if token:
                stack[-1].append(token)
                token = ""
            nested: list = []
            stack[-1].append(nested)
            stack.append(nested)
        elif ch == ")":
            if token:
                stack[-1].append(token)
                token = ""
            if len(stack) == 1:
                raise ValueError(f"unbalanced ')' at offset {i}")
            stack.pop()
        elif ch in WHITESPACE:
            if token:
                stack[-1].append(token)
                token = ""
        else:
            token += ch
        i += 1

    if in_string:
        raise ValueError("unterminated string")
    if len(stack) != 1:
        raise ValueError(f"unbalanced '(': {len(stack) - 1} form(s) left open")
    if token:
        stack[-1].append(token)
    return stack[0]


def split_forms(text: str) -> list[list]:
    """Every top-level parenthesised form, in order."""
    return [item for item in _read(text) if isinstance(item, list)]


def parse_sexpr(text: str) -> list:
    """The first top-level form, or [] if there is none."""
    forms = split_forms(text)
    return forms[0] if forms else []


def form_span(text: str, start: int) -> tuple[int, int]:
    """Character span of the form opening at `start`, strings respected."""
    depth = 0
    in_string = False
    i = start
    n = len(text)

    while i < n:
        ch = text[i]
        if in_string:
            if ch == ESCAPE:
                i += 2
                continue
            if ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1

    raise ValueError(f"form opening at offset {start} is never closed")


def sexpr_to_string(sexpr: list, indent: int = 0) -> str:
    """Render nested lists back to S-expression text."""
    if not isinstance(sexpr, list):
        return str(sexpr)
    if not sexpr:
        return "()"

    has_nested = any(isinstance(item, list) for item in sexpr)
    if not has_nested and len(sexpr) <= 4:
        return "({})".format(" ".join(str(item) for item in sexpr))

    lines = ["("]
    for position, item in enumerate(sexpr):
        if isinstance(item, list):
            lines.append("\t" * (indent + 1) + sexpr_to_string(item, indent + 1))
        elif position == 0:
            lines[0] += str(item)
        else:
            lines.append("\t" * (indent + 1) + str(item))
    lines.append("\t" * indent + ")")
    return "\n".join(lines)


def to_float(value: str | None, default: float = 0.0) -> float:
    """KiCad coordinates that are not numbers should not be fatal."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
