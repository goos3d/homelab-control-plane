import unittest

from fastapi.testclient import TestClient

from controlplane.api.server import app


class WebUiTests(unittest.TestCase):
    def test_role_aware_login_and_minecraft_views_are_present(self):
        response = TestClient(app).get("/")

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('id="login-form"', html)
        self.assertIn('id="admin-view"', html)
        self.assertIn('id="minecraft-view"', html)
        self.assertIn('id="wonton26-whitelist"', html)
        self.assertIn("/api/v1/minecraft/servers", html)
        self.assertIn("/api/v1/minecraft/wonton26/whitelist", html)
        self.assertIn("minecraft.wonton26.whitelist", html)
        self.assertIn("PROTECTED", html)
        self.assertIn("REMOVE ${username} FROM THE WONTON26 WHITELIST", html)


if __name__ == "__main__":
    unittest.main()
