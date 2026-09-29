def _to_base26(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(chr(ord("a") + value % 26))
        value //= 26
    return "".join(reversed(chars))


class FakeTokenSource:
    """Deterministic token source: token(10) yields aaaaaaaaaa, aaaaaaaaab, ..."""

    def __init__(self) -> None:
        self._counter = 0

    def token(self, n: int) -> str:
        value = self._counter
        self._counter += 1
        return _to_base26(value, n)
