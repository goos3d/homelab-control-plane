"""Tests for Minecraft service safety behavior ensuring exclusive server access."""

import unittest
from unittest.mock import Mock

from controlplane.services.minecraft import MinecraftService, MinecraftConflictError


class TestMinecraftSafety(unittest.TestCase):
    def setUp(self):
        self.proxmox = Mock()
        self.minecraft_service = MinecraftService(self.proxmox)
        
    def test_snapshot_when_both_servers_stopped_each_available_to_start(self):
        """Test that when both Minecraft servers are stopped, each server is available to start."""
        # Mock guests where both servers are stopped
        guest104 = Mock(vmid=104, status="stopped", node="proxmox-node", cpu_percent=0, memory_percent=0, uptime_seconds=0)
        guest105 = Mock(vmid=105, status="stopped", node="proxmox-node", cpu_percent=0, memory_percent=0, uptime_seconds=0)
        
        self.proxmox.get_guests.return_value = [guest104, guest105]
        
        result = self.minecraft_service.snapshot(enforce_exclusivity=True)
        
        # Both servers should be available to start
        server104 = next(s for s in result if s["vmid"] == 104)
        server105 = next(s for s in result if s["vmid"] == 105)
        
        self.assertTrue(server104["start_available"])
        self.assertTrue(server105["start_available"])
        self.assertIsNone(server104["unavailable_reason"])
        self.assertIsNone(server105["unavailable_reason"])

    def test_snapshot_when_vm104_running_vm105_not_available(self):
        """Test that when VM 104 is running, VM 105 is not available to start."""
        # Mock guests where vm104 is running and vm105 is stopped
        guest104 = Mock(vmid=104, status="running", node="proxmox-node", cpu_percent=0, memory_percent=0, uptime_seconds=0)
        guest105 = Mock(vmid=105, status="stopped", node="proxmox-node", cpu_percent=0, memory_percent=0, uptime_seconds=0)
        
        self.proxmox.get_guests.return_value = [guest104, guest105]
        
        result = self.minecraft_service.snapshot(enforce_exclusivity=True)
        
        # VM 105 should not be available to start
        server105 = next(s for s in result if s["vmid"] == 105)
        
        self.assertFalse(server105["start_available"])
        self.assertIsNotNone(server105["unavailable_reason"])
        # The message is about the other server that's running.
        self.assertIn("Minecraft VM 104 is currently running", server105["unavailable_reason"])

    def test_snapshot_when_vm105_running_vm104_not_available(self):
        """Test that when VM 105 is running, VM 104 is not available to start."""
        # Mock guests where vm105 is running and vm104 is stopped
        guest104 = Mock(vmid=104, status="stopped", node="proxmox-node", cpu_percent=0, memory_percent=0, uptime_seconds=0)
        guest105 = Mock(vmid=105, status="running", node="proxmox-node", cpu_percent=0, memory_percent=0, uptime_seconds=0)
        
        self.proxmox.get_guests.return_value = [guest104, guest105]
        
        result = self.minecraft_service.snapshot(enforce_exclusivity=True)
        
        # VM 104 should not be available to start
        server104 = next(s for s in result if s["vmid"] == 104)
        
        self.assertFalse(server104["start_available"])
        self.assertIsNotNone(server104["unavailable_reason"])
        # The message is about the other server that's running.
        self.assertIn("Minecraft VM 105 is currently running", server104["unavailable_reason"])

    def test_snapshot_unavailable_reason_explains_why_start_unavailable(self):
        """Test that unavailable_reason explains why start is unavailable."""
        # Mock guests where vm105 is running and vm104 is stopped
        guest104 = Mock(vmid=104, status="stopped", node="proxmox-node", cpu_percent=0, memory_percent=0, uptime_seconds=0)
        guest105 = Mock(vmid=105, status="running", node="proxmox-node", cpu_percent=0, memory_percent=0, uptime_seconds=0)
        
        self.proxmox.get_guests.return_value = [guest104, guest105]
        
        result = self.minecraft_service.snapshot(enforce_exclusivity=True)
        
        server104 = next(s for s in result if s["vmid"] == 104)
        
        # Should have a clear reason - checking the actual message format from implementation
        self.assertTrue(
            "Minecraft VM 105 is currently running" in server104["unavailable_reason"]
        )

    def test_action_rejects_start_when_other_server_running(self):
        """Test that attempting to start one server while the other is running is rejected before Proxmox operation."""
        # Mock guests where vm105 is running and vm104 is stopped
        guest104 = Mock(vmid=104, status="stopped", node="proxmox-node", cpu_percent=0, memory_percent=0, uptime_seconds=0)
        guest105 = Mock(vmid=105, status="running", node="proxmox-node", cpu_percent=0, memory_percent=0, uptime_seconds=0)
        
        self.proxmox.get_guests.return_value = [guest104, guest105]
        
        # This should raise a MinecraftConflictError before calling proxmox
        with self.assertRaises(MinecraftConflictError) as context:
            self.minecraft_service.action(104, "start", admin_override=False)
            
        # Should specify which server is running
        self.assertIn("Minecraft VM 105 is currently running", str(context.exception))
        
        # Proxmox should not be called since the operation was rejected early
        self.proxmox.guest_action.assert_not_called()
