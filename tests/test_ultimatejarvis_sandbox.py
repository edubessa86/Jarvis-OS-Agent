import unittest

from ultimatejarvis.sandbox import (
    IsolationCapabilities,
    IsolationProfile,
    IsolationUnavailableError,
    require_isolation,
)


class IsolationPolicyTests(unittest.TestCase):
    def test_profile_defaults_are_restrictive_and_bounded(self):
        profile = IsolationProfile()
        self.assertFalse(profile.network_enabled)
        self.assertTrue(profile.workspace_read_only)
        self.assertEqual(profile.memory_limit_mb, 512)
        self.assertEqual(profile.timeout_seconds, 15)

    def test_profile_rejects_network_and_writable_workspace(self):
        with self.assertRaises(ValueError):
            IsolationProfile(network_enabled=True)
        with self.assertRaises(ValueError):
            IsolationProfile(workspace_read_only=False)

    def test_profile_rejects_unbounded_resource_values(self):
        with self.assertRaises(ValueError):
            IsolationProfile(memory_limit_mb=0)
        with self.assertRaises(ValueError):
            IsolationProfile(timeout_seconds=0)
        with self.assertRaises(ValueError):
            IsolationProfile(max_output_bytes=10_000_000)

    def test_missing_backend_fails_closed(self):
        with self.assertRaises(IsolationUnavailableError):
            require_isolation(None)

    def test_partial_capabilities_fail_closed(self):
        capabilities = IsolationCapabilities(
            process_isolation=True,
            network_blocked=True,
            filesystem_read_only=True,
            memory_limit_enforced=True,
            timeout_enforced=True,
            output_limit_enforced=False,
        )
        with self.assertRaisesRegex(IsolationUnavailableError, "output_limit_enforced"):
            require_isolation(capabilities)

    def test_all_confirmed_capabilities_are_accepted(self):
        capabilities = IsolationCapabilities(
            process_isolation=True,
            network_blocked=True,
            filesystem_read_only=True,
            memory_limit_enforced=True,
            timeout_enforced=True,
            output_limit_enforced=True,
        )
        self.assertIs(require_isolation(capabilities), capabilities)


if __name__ == "__main__":
    unittest.main()
