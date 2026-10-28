import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from controlplane.services.system import SystemService


class SystemServiceTests(unittest.TestCase):
    def test_controller_health_uses_local_linux_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "meminfo").write_text(
                "MemTotal:       2048 kB\nMemAvailable:    512 kB\n"
            )
            (root / "uptime").write_text("90061.25 0.00\n")
            service = SystemService(
                proc_root=root,
                disk_path=root,
                hostname_getter=lambda: "controller.example.test",
            )
            service._cpu_percent = Mock(return_value=12.5)

            with (
                patch("controlplane.services.system.os.cpu_count", return_value=2),
                patch(
                    "controlplane.services.system.shutil.disk_usage",
                    return_value=Mock(total=1000, used=250),
                ),
            ):
                status = service.get_status()

        self.assertEqual(status.hostname, "controller.example.test")
        self.assertEqual(status.status, "online")
        self.assertEqual(status.api_status, "online")
        self.assertEqual(status.cpu_cores, 2)
        self.assertEqual(status.cpu_percent, 12.5)
        self.assertEqual(status.memory_percent, 75.0)
        self.assertEqual(status.disk_percent, 25.0)
        self.assertEqual(status.uptime_seconds, 90061)
