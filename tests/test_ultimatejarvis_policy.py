import unittest

from ultimatejarvis.policy import Decision, Risk, ToolRequest, authorize


class PolicyTests(unittest.TestCase):
    def test_read_only_is_allowed(self):
        result = authorize(ToolRequest("workspace.read", Risk.READ_ONLY))
        self.assertEqual(result.decision, Decision.ALLOW)

    def test_workspace_write_is_allowed_for_path_checked_tool(self):
        result = authorize(ToolRequest("workspace.write", Risk.WORKSPACE_WRITE))
        self.assertEqual(result.decision, Decision.ALLOW)
        self.assertIn("path validation", result.reason)

    def test_execution_without_sandbox_is_denied(self):
        result = authorize(ToolRequest("python.run", Risk.SANDBOX_EXECUTION))
        self.assertEqual(result.decision, Decision.DENY)

    def test_sandbox_execution_requires_approval(self):
        result = authorize(
            ToolRequest("python.run", Risk.SANDBOX_EXECUTION, in_sandbox=True)
        )
        self.assertEqual(result.decision, Decision.REQUIRE_APPROVAL)

    def test_approved_sandbox_execution_is_allowed(self):
        result = authorize(
            ToolRequest(
                "python.run",
                Risk.SANDBOX_EXECUTION,
                in_sandbox=True,
                approved=True,
            )
        )
        self.assertEqual(result.decision, Decision.ALLOW)

    def test_external_side_effect_requires_approval(self):
        result = authorize(ToolRequest("email.send", Risk.EXTERNAL_SIDE_EFFECT))
        self.assertEqual(result.decision, Decision.REQUIRE_APPROVAL)

    def test_empty_tool_name_is_denied(self):
        result = authorize(ToolRequest(" ", Risk.READ_ONLY))
        self.assertEqual(result.decision, Decision.DENY)


if __name__ == "__main__":
    unittest.main()
