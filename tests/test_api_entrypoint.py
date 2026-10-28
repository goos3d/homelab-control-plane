import os
import unittest
from unittest.mock import patch

from controlplane.api.__main__ import main


class ApiEntrypointTests(unittest.TestCase):
    def test_api_defaults_to_localhost(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "controlplane.api.__main__.uvicorn.run"
        ) as run:
            main()

        run.assert_called_once_with(
            "controlplane.api.server:app",
            host="127.0.0.1",
            port=8000,
        )

    def test_api_listener_is_configurable(self):
        with patch.dict(
            os.environ,
            {"CONTROL_PLANE_API_HOST": "0.0.0.0", "CONTROL_PLANE_API_PORT": "9000"},
            clear=True,
        ), patch("controlplane.api.__main__.uvicorn.run") as run:
            main()

        run.assert_called_once_with(
            "controlplane.api.server:app",
            host="0.0.0.0",
            port=9000,
        )

    def test_invalid_api_port_fails_before_starting_server(self):
        with patch.dict(
            os.environ,
            {"CONTROL_PLANE_API_PORT": "invalid"},
            clear=True,
        ), patch("controlplane.api.__main__.uvicorn.run") as run:
            with self.assertRaisesRegex(ValueError, "must be an integer"):
                main()

        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
