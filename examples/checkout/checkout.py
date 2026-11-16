"""Calculate an order total from quantities and unit prices."""


def calculate_total(items, discount_percent=0, shipping=0):
    """Apply the percentage discount to merchandise, then add shipping."""
    subtotal = sum(quantity * unit_price for quantity, unit_price in items)
    return round((subtotal + shipping) * (1 - discount_percent / 100), 2)
