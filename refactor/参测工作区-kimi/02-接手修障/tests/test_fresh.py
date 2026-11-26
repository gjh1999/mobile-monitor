import unittest

from subscriptions import configure, current, status


class FreshConfig(unittest.TestCase):
    def test_toggle(self):
        data = {"subscriptions": []}
        self.assertTrue(configure(data, "lab", True))
        self.assertEqual(data["subscriptions"], [current("lab")])
        self.assertEqual(status(data, "lab"), "current")
        self.assertFalse(configure(data, "lab", True))
        self.assertTrue(configure(data, "lab", False))
        self.assertEqual(status(data, "lab"), "missing")
