"""Make values safe to interpolate into a log line."""


def log_safe(value: object) -> str:
    """`value` as text with CR and LF escaped, so it cannot start a forged log entry.

    Model names, settings values and provider responses reach log messages; a
    newline in any of them would otherwise split one entry into two.
    """
    return str(value).replace("\r", "\\r").replace("\n", "\\n")
