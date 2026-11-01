"""Versioned, conservative text comparison shared by every objective test."""

COMPARISON_POLICY_VERSION = "text-v1"


def normalize_output(output: str) -> str:
    """Normalize CRLF/bare CR, then trim ASCII whitespace at the entire end.

    Leading whitespace, internal blank lines, indentation, per-line trailing
    spaces and tabs remain significant. Final blank lines/newlines are ignored.
    Unicode whitespace, case, punctuation and numeric formatting are unchanged.
    """
    return output.replace("\r\n", "\n").replace("\r", "\n").rstrip(" \t\n\r\v\f")


def outputs_match(actual: str, expected: str) -> bool:
    return normalize_output(actual) == normalize_output(expected)
