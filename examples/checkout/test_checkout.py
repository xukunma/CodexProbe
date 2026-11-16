import unittest

from checkout import calculate_total


class CalculateTotalTests(unittest.TestCase):
    def test_multiple_items(self):
        self.assertEqual(calculate_total([(2, 12.5), (1, 10)]), 35)

    def test_discount_without_shipping(self):
        self.assertEqual(calculate_total([(2, 12.5), (1, 10)], 10), 31.5)

    def test_shipping_without_discount(self):
        self.assertEqual(calculate_total([(2, 12.5), (1, 10)], shipping=5), 40)

    def test_shipping_is_added_after_discount(self):
        self.assertEqual(calculate_total([(2, 12.5), (1, 10)], 10, 5), 36.5)

    def test_full_discount_still_charges_shipping(self):
        self.assertEqual(calculate_total([(1, 20)], 100, 5), 5)

    def test_empty_order_still_charges_shipping(self):
        self.assertEqual(calculate_total([], 10, 5), 5)

    def test_rounds_to_two_decimal_places(self):
        self.assertEqual(calculate_total([(3, 1.99)], 15), 5.07)


if __name__ == "__main__":
    unittest.main()
