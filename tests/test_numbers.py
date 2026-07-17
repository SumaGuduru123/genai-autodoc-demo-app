"""
tests/test_numbers.py
---------------------
Unit tests for basic arithmetic operations on two numbers.
Run with: pytest tests/test_numbers.py -v
"""

import pytest


# ---------------------------------------------------------------------------
# Helper functions under test
# ---------------------------------------------------------------------------

def add(a: float, b: float) -> float:
    """Return the sum of a and b."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Return a minus b."""
    return a - b


def multiply(a: float, b: float) -> float:
    """Return the product of a and b."""
    return a * b


def divide(a: float, b: float) -> float:
    """Return a divided by b. Raises ValueError if b is zero."""
    if b == 0:
        raise ValueError("Cannot divide by zero.")
    return a / b


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAdd:
    def test_positive_numbers(self):
        assert add(3, 5) == 8

    def test_negative_numbers(self):
        assert add(-4, -6) == -10

    def test_mixed_sign(self):
        assert add(-3, 7) == 4

    def test_floats(self):
        assert add(1.5, 2.5) == pytest.approx(4.0)

    def test_zero(self):
        assert add(0, 0) == 0


class TestSubtract:
    def test_positive_numbers(self):
        assert subtract(10, 4) == 6

    def test_result_negative(self):
        assert subtract(3, 9) == -6

    def test_same_numbers(self):
        assert subtract(7, 7) == 0

    def test_floats(self):
        assert subtract(5.5, 2.2) == pytest.approx(3.3)


class TestMultiply:
    def test_positive_numbers(self):
        assert multiply(3, 4) == 12

    def test_by_zero(self):
        assert multiply(99, 0) == 0

    def test_negative_numbers(self):
        assert multiply(-3, -4) == 12

    def test_mixed_sign(self):
        assert multiply(-3, 4) == -12

    def test_floats(self):
        assert multiply(2.5, 4.0) == pytest.approx(10.0)


class TestDivide:
    def test_positive_numbers(self):
        assert divide(10, 2) == pytest.approx(5.0)

    def test_result_float(self):
        assert divide(7, 2) == pytest.approx(3.5)

    def test_negative_dividend(self):
        assert divide(-9, 3) == pytest.approx(-3.0)

    def test_divide_by_zero_raises(self):
        with pytest.raises(ValueError, match="Cannot divide by zero"):
            divide(5, 0)

    def test_floats(self):
        assert divide(7.5, 2.5) == pytest.approx(3.0)
