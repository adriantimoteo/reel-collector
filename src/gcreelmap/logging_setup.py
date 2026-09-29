import logging

_FORMAT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    """Configure root logging.

    Policy (D11): log records must never contain Telegram message text.
    Enforced by convention in this phase; a canary test lands in P8.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
