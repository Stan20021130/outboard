import unittest

from sample_app.api import prepare_request


class TimeoutTests(unittest.TestCase):
    def test_environment_timeout_propagates(self):
        self.assertEqual(prepare_request({"API_TIMEOUT_SECONDS": "7"})["timeout"], 7)

    def test_request_timeout_overrides_settings(self):
        self.assertEqual(prepare_request({"API_TIMEOUT_SECONDS": "7"}, 2)["timeout"], 2)

    def test_default_timeout(self):
        self.assertEqual(prepare_request({})["timeout"], 30)

