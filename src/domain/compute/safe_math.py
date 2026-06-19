"""Safe math utilities — prevent division-by-zero and handle None inputs gracefully."""
from typing import Optional


def safe_divide(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """Return numerator / denominator or None if denominator is zero or either is None."""
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


def safe_subtract(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return a - b


def safe_add(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return a + b


def clamp(value: Optional[float], lo: float = 0.0, hi: float = 100.0) -> Optional[float]:
    """Clamp value to [lo, hi] range."""
    if value is None:
        return None
    return max(lo, min(hi, value))
