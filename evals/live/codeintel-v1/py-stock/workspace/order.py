from inventory import is_available

def can_order(stock: int) -> bool:
    return is_available(stock)
