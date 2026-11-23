"""Deliberately faulty negative control for the clamp source contract."""


def clamp(value, lower, upper):
    if value < lower:
        return lower
    if value > upper:
        return lower
    return value
