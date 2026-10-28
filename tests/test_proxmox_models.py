import unittest
from unittest.mock import Mock, patch

from controlplane.services.proxmox import ProxmoxService


class ProxmoxNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.service = ProxmoxService.__new__(ProxmoxService)
        self.service._get = Mock(
            side_effect=lambda path: {
                "/nodes": [
                    {
                        "node": "proxmox-node",
                        "status": "online",
                        "cpu": 0.125,
                        "maxcpu": 8,
                        "mem": 8,
                        "maxmem": 32,
                        "disk": 25,
                        "maxdisk": 100,
                        "uptime": 3600,
                    }
                ],
                "/nodes/proxmox-node/qemu": [
                    {
                        "vmid": 102,
                        "name": "minecraft-minecraft-server-104",
                        "status": "running",
                        "cpu": 0.25,
                        "mem": 12,
                        "maxmem": 16,
                        "uptime": 600,
                    }
                ],
                "/nodes/proxmox-node/lxc": [
                    {"vmid": 101, "status": "stopped"}
                ],
            }[path]
        )

    def test_dashboard_normalizes_node_and_guests(self):
        snapshot = self.service.get_dashboard("proxmox-node")

        self.assertEqual(snapshot.node.name, "proxmox-node")
        self.assertEqual(snapshot.node.cpu_percent, 12.5)
        self.assertEqual(snapshot.node.memory_percent, 25.0)
        self.assertEqual(snapshot.node.uptime_seconds, 3600)
        self.assertEqual([guest.vmid for guest in snapshot.guests], [101, 102])

        minecraft = snapshot.guests[1]
        self.assertEqual(minecraft.type, "vm")
        self.assertEqual(minecraft.cpu_percent, 25.0)
        self.assertEqual(minecraft.memory_percent, 75.0)
        self.assertEqual(minecraft.uptime_seconds, 600)

    def test_dashboard_reads_each_live_resource_once(self):
        self.service.get_dashboard("proxmox-node")

        self.assertEqual(
            [call.args[0] for call in self.service._get.call_args_list],
            ["/nodes", "/nodes/proxmox-node/qemu", "/nodes/proxmox-node/lxc"],
        )

    def test_dashboard_requires_configured_node_when_not_explicit(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "PVE_NODE"):
                self.service.get_dashboard()

    def test_backup_submission_is_snapshot_only_and_has_no_prune_fields(self):
        self.service._post = Mock(return_value="UPID:proxmox-node:test:")
        task = self.service.start_guest_backup(
            node="proxmox-node",
            vmid=104,
            storage="backup-storage",
        )
        self.assertEqual(task, "UPID:proxmox-node:test:")
        path = self.service._post.call_args.args[0]
        data = self.service._post.call_args.kwargs["data"]
        self.assertEqual(path, "/nodes/proxmox-node/vzdump")
        self.assertEqual(
            data,
            {
                "vmid": 104,
                "storage": "backup-storage",
                "mode": "snapshot",
                "compress": "zstd",
            },
        )
        self.assertNotIn("remove", data)
        self.assertNotIn("prune-backups", data)

    def test_backup_artifact_query_is_scoped_to_guest(self):
        self.service._get = Mock(return_value=[])
        self.service.get_backup_artifacts(
            node="proxmox-node",
            storage="backup-storage",
            vmid=104,
        )
        self.service._get.assert_called_once_with(
            "/nodes/proxmox-node/storage/backup-storage/content?content=backup&vmid=104"
        )
