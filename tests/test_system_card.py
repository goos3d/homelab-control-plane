import unittest

from controlplane.ui.widgets import SystemCard


class SystemCardTests(unittest.TestCase):
    def test_build_text_normalizes_status_and_metrics(self):
        text = SystemCard.build_text(
            "Controller",
            "online",
            {"CPU": "3.0%", "UP": "2D 4H"},
        )

        self.assertEqual(
            text.plain,
            "CONTROLLER  ONLINE  //  CPU 3.0%  //  UP 2D 4H",
        )
