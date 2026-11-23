import asyncio
import unittest

from gateway import GatewayState


def run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coro)


class GatewayContractTests(unittest.TestCase):
    def setUp(self):
        self.state = GatewayState()

    def test_initial_state_matches_controller_contract(self):
        self.assertEqual(self.state.payload(), {"mode": "normal", "backends": {"app1": {"enabled": True, "weight": 100}, "app2": {"enabled": True, "weight": 100}, "app3": {"enabled": True, "weight": 100}}})

    def test_disable_is_idempotent_and_removes_backend_from_routing(self):
        run(self.state.update_backend("app2", "disable", {}, "/admin/backends/app2/disable"))
        run(self.state.update_backend("app2", "disable", {}, "/admin/backends/app2/disable"))
        selected = [run(self.state.choose()) for _ in range(20)]
        self.assertNotIn("app2", selected)
        self.assertEqual(self.state.payload()["backends"]["app2"], {"enabled": False, "weight": 100})

    def test_weight_zero_removes_backend_and_command_is_recorded(self):
        run(self.state.update_backend("app3", "weight", {"weight": 0}, "/admin/backends/app3/weight"))
        selected = [run(self.state.choose()) for _ in range(20)]
        self.assertNotIn("app3", selected)
        self.assertEqual(self.state.calls[-1]["body"], {"weight": 0})

    def test_weighted_selection_uses_the_configured_ratio(self):
        run(self.state.update_backend("app2", "weight", {"weight": 25}, "/admin/backends/app2/weight"))
        selected = [run(self.state.choose()) for _ in range(225)]
        self.assertEqual(selected.count("app1"), 100)
        self.assertEqual(selected.count("app2"), 25)
        self.assertEqual(selected.count("app3"), 100)

    def test_mode_command_is_idempotent_and_recorded(self):
        run(self.state.update_mode("emergency", "/admin/mode", {"mode": "emergency"}))
        run(self.state.update_mode("emergency", "/admin/mode", {"mode": "emergency"}))
        self.assertEqual(self.state.payload()["mode"], "emergency")
        self.assertEqual(len(self.state.calls), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
