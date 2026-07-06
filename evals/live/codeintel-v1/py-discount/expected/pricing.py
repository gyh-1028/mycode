def apply_discount(price: float, percent: int) -> float:
    return price * (1 - percent / 100)
