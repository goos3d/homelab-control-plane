import unittest
from unittest.mock import Mock, patch

from controlplane.services.nas import NasService


class NasServiceTests(unittest.TestCase):
    def test_mounted_share_reports_live_storage(self):
        service = NasService("/mnt/nas")
        with (
            patch("controlplane.services.nas.os.path.ismount", return_value=True),
            patch(
                "controlplane.services.nas.shutil.disk_usage",
                return_value=Mock(total=1000, used=250, free=750),
            ),
        ):
            status = service.get_status()

        self.assertEqual(status.status, "online")
        self.assertEqual(status.mount_path, "/mnt/nas")
        self.assertEqual(status.storage_used_bytes, 250)
        self.assertEqual(status.storage_total_bytes, 1000)
        self.assertEqual(status.storage_free_bytes, 750)
        self.assertEqual(status.storage_percent, 25.0)

    def test_unmounted_share_reports_offline_without_disk_lookup(self):
        service = NasService("/mnt/nas")
        with (
            patch("controlplane.services.nas.os.path.ismount", return_value=False),
            patch("controlplane.services.nas.shutil.disk_usage") as disk_usage,
        ):
            status = service.get_status()

        self.assertEqual(status.status, "offline")
        self.assertIn("not mounted", status.detail)
        disk_usage.assert_not_called()
