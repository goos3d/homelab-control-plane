import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controlplane.services.fambam import (
    FambamConfiguration,
    FambamConfigurationError,
    FambamService,
)


def configuration(
    *,
    host="server.example.test",
    port=25565,
    mac="02:00:00:00:00:01",
    ssh_user="control-user",
    ssh_key=Path("/tmp/control-plane-test-key"),
):
    return FambamConfiguration(host, port, mac, ssh_user, ssh_key)


class FambamServiceTests(unittest.TestCase):
    def test_environment_configuration_uses_no_homelab_address_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = FambamConfiguration.from_environment()

        self.assertEqual(settings.host, "")
        self.assertEqual(settings.port, 25565)
        self.assertEqual(settings.mac, "")
        self.assertEqual(settings.ssh_user, "")

    def test_wake_on_lan_validates_configuration_before_calling_command(self):
        service = FambamService(configuration(host=""))
        with patch("controlplane.services.fambam.subprocess.run") as run:
            with self.assertRaisesRegex(FambamConfigurationError, "HOST"):
                service.start()
        run.assert_not_called()

    def test_wake_on_lan_uses_configured_mac(self):
        service = FambamService(configuration())
        with patch("controlplane.services.fambam.subprocess.run") as run:
            service.start()
        self.assertEqual(run.call_args.args[0], ["wakeonlan", "02:00:00:00:00:01"])

    def test_shutdown_validates_key_before_calling_ssh(self):
        service = FambamService(configuration(ssh_key=Path("/missing/key")))
        with patch("controlplane.services.fambam.subprocess.Popen") as popen:
            with self.assertRaisesRegex(FambamConfigurationError, "key file"):
                service.shutdown()
        popen.assert_not_called()

    def test_shutdown_rejects_unreadable_key_before_calling_ssh(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.touch()
            service = FambamService(configuration(ssh_key=key))
            with (
                patch("controlplane.services.fambam.os.access", return_value=False),
                patch("controlplane.services.fambam.subprocess.Popen") as popen,
            ):
                with self.assertRaisesRegex(FambamConfigurationError, "not readable"):
                    service.shutdown()
        popen.assert_not_called()

    def test_wake_on_lan_rejects_invalid_mac_before_calling_command(self):
        service = FambamService(configuration(mac="invalid"))
        with patch("controlplane.services.fambam.subprocess.run") as run:
            with self.assertRaisesRegex(FambamConfigurationError, "MAC"):
                service.start()
        run.assert_not_called()

    def test_shutdown_uses_configured_ssh_user_and_key(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.touch()
            service = FambamService(configuration(ssh_key=key))
            with patch("controlplane.services.fambam.subprocess.Popen") as popen:
                service.shutdown()

        command = popen.call_args.args[0]
        self.assertIn(str(key), command)
        self.assertEqual(command[-1], "control-user@server.example.test")
        self.assertIn("StrictHostKeyChecking=yes", command)

    def test_snapshot_disables_start_when_connection_settings_are_missing(self):
        snapshot = FambamService(configuration(host="")).snapshot()
        self.assertFalse(snapshot["start_available"])
        self.assertEqual(snapshot["status"], "stopped")
        self.assertIsNotNone(snapshot["unavailable_reason"])

    def test_snapshot_disables_start_when_wake_on_lan_settings_are_missing(self):
        service = FambamService(configuration(mac=""))
        with patch.object(service, "is_online", return_value=False):
            snapshot = service.snapshot()

        self.assertFalse(snapshot["start_available"])
        self.assertIsNotNone(snapshot["unavailable_reason"])


if __name__ == "__main__":
    unittest.main()
