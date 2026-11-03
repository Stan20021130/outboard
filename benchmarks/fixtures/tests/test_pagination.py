import unittest

from sample_app.pagination import page_items


class PaginationTests(unittest.TestCase):
    def test_full_page_has_no_extra_item(self):
        self.assertEqual(page_items(list(range(10)), 1, 3), [0, 1, 2])

    def test_last_page_has_remaining_items(self):
        self.assertEqual(page_items(list(range(10)), 4, 3), [9])

    def test_empty_page(self):
        self.assertEqual(page_items(list(range(10)), 5, 3), [])

