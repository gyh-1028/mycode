def invert(values: dict[str, int]) -> dict[int, str]:
    return {value: key for key, value in values.items()}
