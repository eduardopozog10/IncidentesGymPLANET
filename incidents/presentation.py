"""Lossless Chilean decimal display, independent of process locale."""
from decimal import Decimal, InvalidOperation


def format_quantity(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return '—'
    except (InvalidOperation, ValueError):
        return '—'
    text = format(number, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text.replace('.', ',')
