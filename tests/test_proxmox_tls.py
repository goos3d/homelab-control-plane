import os
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from controlplane.services.proxmox import ProxmoxService


class ProxmoxTlsConfigurationTests(unittest.TestCase):
    def service(self, **environment):
        values = {
            "PVE_TLS_VERIFY": "true",
            "PVE_TLS_CA_FILE": "",
        }
        values.update(environment)
        with patch.dict(os.environ, values, clear=False):
            return ProxmoxService()

    def test_tls_verification_is_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            service = ProxmoxService()

        self.assertIs(service.tls_verify, True)

    def test_custom_ca_is_added_to_default_trust_context(self):
        with tempfile.TemporaryDirectory() as directory:
            ca_file = Path(directory) / "proxmox-ca.pem"
            ca_file.write_text("test CA", encoding="utf-8")
            context = ssl.create_default_context()
            with patch(
                "controlplane.services.proxmox.ssl.create_default_context",
                return_value=context,
            ) as create_context:
                service = self.service(PVE_TLS_CA_FILE=str(ca_file))

        create_context.assert_called_once_with(cafile=str(ca_file))
        self.assertIs(service.tls_verify, context)

    def test_tls_setting_is_passed_to_both_http_clients(self):
        service = self.service()
        service.base_url = "https://proxmox.example.test:8006/api2/json"
        service.token_id = "test-token"
        service.token_secret = "test-secret"
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get.return_value.json.return_value = {"data": []}
        client.post.return_value.json.return_value = {"data": "task"}
        with patch("controlplane.services.proxmox.httpx.Client", return_value=client) as factory:
            service._get("/nodes")
            service._post("/nodes/test/start")

        self.assertEqual(factory.call_args_list[0].kwargs["verify"], True)
        self.assertEqual(factory.call_args_list[1].kwargs["verify"], True)

    def test_custom_ca_context_is_passed_to_http_client(self):
        with tempfile.TemporaryDirectory() as directory:
            ca_file = Path(directory) / "proxmox-ca.pem"
            ca_file.write_text("test CA", encoding="utf-8")
            context = ssl.create_default_context()
            with patch(
                "controlplane.services.proxmox.ssl.create_default_context",
                return_value=context,
            ):
                service = self.service(PVE_TLS_CA_FILE=str(ca_file))

        service.base_url = "https://proxmox.example.test:8006/api2/json"
        service.token_id = "test-token"
        service.token_secret = "test-secret"
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get.return_value.json.return_value = {"data": []}
        with patch("controlplane.services.proxmox.httpx.Client", return_value=client) as factory:
            service._get("/nodes")

        self.assertIs(factory.call_args.kwargs["verify"], context)

    def test_explicit_verification_opt_out_is_supported(self):
        service = self.service(PVE_TLS_VERIFY="false")
        self.assertIs(service.tls_verify, False)

    def test_ca_cannot_be_configured_with_verification_disabled(self):
        with tempfile.NamedTemporaryFile() as ca_file:
            with self.assertRaisesRegex(RuntimeError, "cannot be used"):
                self.service(
                    PVE_TLS_VERIFY="false",
                    PVE_TLS_CA_FILE=ca_file.name,
                )

    def test_missing_ca_file_fails_configuration(self):
        with self.assertRaisesRegex(RuntimeError, "CA file"):
            self.service(PVE_TLS_CA_FILE="/missing/proxmox-ca.pem")

    def test_invalid_tls_boolean_fails_configuration(self):
        with self.assertRaisesRegex(RuntimeError, "boolean"):
            self.service(PVE_TLS_VERIFY="sometimes")


if __name__ == "__main__":
    unittest.main()
