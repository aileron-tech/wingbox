"""S-expression facts of the architecture: quoting, reading one line, keyword values."""
from __future__ import annotations

from typing import Optional


# Every character that ends a line for a reader of text (str.splitlines): a fact is one line.
_LINE_BREAKS = dict.fromkeys(map(ord, "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"), " ")


def q(text: str) -> str:
    """A quoted S-expression string on one line: a line break of any kind becomes a space."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').translate(_LINE_BREAKS) + '"'


def parse(line: str) -> list[str]:
    """Tokens of one flat fact `(head a "b c" :k v)`; quoted strings are unquoted."""
    text = line.strip()
    if not (text.startswith("(") and text.endswith(")")):
        return []
    inner = text[1:-1]
    tokens: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == " ":
            i += 1
        elif ch == '"':
            j = i + 1
            chars: list[str] = []
            while j < len(inner) and inner[j] != '"':
                if inner[j] == "\\" and j + 1 < len(inner):
                    j += 1
                chars.append(inner[j])
                j += 1
            tokens.append("".join(chars))
            i = j + 1
        else:
            j = i
            while j < len(inner) and inner[j] != " ":
                j += 1
            tokens.append(inner[i:j])
            i = j
    return tokens


def keyword(tokens: list[str], name: str, default: Optional[str] = None) -> Optional[str]:
    """Value after `:name` in a token list."""
    try:
        index = tokens.index(":" + name)
    except ValueError:
        return default
    return tokens[index + 1] if index + 1 < len(tokens) else default


def positional(tokens: list[str]) -> list[str]:
    """Tokens before the first keyword, without the head."""
    values: list[str] = []
    for token in tokens[1:]:
        if token.startswith(":"):
            break
        values.append(token)
    return values
