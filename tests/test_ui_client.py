import unittest

import httpx

from controlplane.ui.client import ControlPlaneClient, ControlPlaneError


class ControlPlaneClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_bearer_token_is_sent_to_admin_api(self):
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"jobs": [], "executions": []}, request=request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = ControlPlaneClient("http://control-plane", token="signed-token", client=http)
        await client.get_automation()
        self.assertEqual(requests[0].headers["Authorization"], "Bearer signed-token")
        await http.aclose()

    async def test_dashboard_snapshot_is_returned_in_vmid_order(self):
        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "node": {"name": "proxmox-node", "status": "online"},
                    "controller": {
                        "name": "Controller",
                        "status": "online",
                    },
                    "nas": {"name": "NAS", "status": "online"},
                    "guests": [{"vmid": 102}, {"vmid": 101}],
                },
                request=request,
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = ControlPlaneClient("http://control-plane", client=http)
        node, guests, controller, nas = await client.get_dashboard()

        self.assertEqual(node["name"], "proxmox-node")
        self.assertEqual(controller["status"], "online")
        self.assertEqual(nas["status"], "online")
        self.assertEqual([guest["vmid"] for guest in guests], [101, 102])
        await http.aclose()

    async def test_vmid_100_is_blocked_without_network_call(self):
        calls = []

        async def handler(request):
            calls.append(request)
            return httpx.Response(500, request=request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = ControlPlaneClient("http://control-plane", client=http)
        with self.assertRaisesRegex(ControlPlaneError, "protected"):
            await client.run_action(100, "shutdown", "proxmox-node")
        self.assertEqual(calls, [])
        await http.aclose()

    async def test_action_is_polled_until_success(self):
        responses = iter(
            [
                {"accepted": True, "task": "UPID:proxmox-node:test:"},
                {"task": {"status": "running"}},
                {"task": {"status": "stopped", "exitstatus": "OK"}},
            ]
        )

        async def handler(request):
            return httpx.Response(200, json=next(responses), request=request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = ControlPlaneClient("http://control-plane", client=http)
        result = await client.run_action(
            102, "reboot", "proxmox-node", poll_interval=0, poll_timeout=1
        )
        self.assertEqual(result.exit_status, "OK")
        self.assertEqual(result.vmid, 102)
        self.assertGreaterEqual(result.elapsed_seconds, 0)
        await http.aclose()

    async def test_progress_reports_running_task(self):
        responses = iter(
            [
                {"accepted": True, "task": "UPID:proxmox-node:test:"},
                {"task": {"status": "running"}},
                {"task": {"status": "stopped", "exitstatus": "OK"}},
            ]
        )
        progress = []

        async def handler(request):
            return httpx.Response(200, json=next(responses), request=request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = ControlPlaneClient("http://control-plane", client=http)
        await client.run_action(
            102,
            "shutdown",
            "proxmox-node",
            poll_interval=0,
            poll_timeout=1,
            progress=lambda elapsed, state: progress.append((elapsed, state)),
        )
        self.assertEqual([state for _, state in progress], ["running", "stopped"])
        await http.aclose()

    async def test_backup_job_is_submitted_through_fixed_endpoint(self):
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx.Response(
                202,
                json={
                    "accepted": True,
                    "execution_id": "execution-1",
                    "state": "queued",
                },
                request=request,
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = ControlPlaneClient("http://control-plane", client=http)
        execution_id = await client.run_minecraft_backup()
        self.assertEqual(execution_id, "execution-1")
        self.assertEqual(
            requests[0].url.path,
            "/api/v1/automation/jobs/minecraft-vm-backup/run",
        )
        await http.aclose()

    async def test_failed_task_is_reported(self):
        responses = iter(
            [
                {"accepted": True, "task": "UPID:proxmox-node:test:"},
                {"task": {"status": "stopped", "exitstatus": "ERROR"}},
            ]
        )

        async def handler(request):
            return httpx.Response(200, json=next(responses), request=request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = ControlPlaneClient("http://control-plane", client=http)
        with self.assertRaisesRegex(ControlPlaneError, "ERROR"):
            await client.run_action(
                102, "reboot", "proxmox-node", poll_interval=0, poll_timeout=1
            )
        await http.aclose()
