"""A small executable source fixture for the initial proof backend."""


def clamp(value, lower, upper):
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value
