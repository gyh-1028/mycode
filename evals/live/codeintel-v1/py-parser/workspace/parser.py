def parse_pair(raw: str) -> tuple[str, str]:
    left, right = raw.split(":", 1)
    return right, left
