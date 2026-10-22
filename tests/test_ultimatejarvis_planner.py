import json
import unittest
from ultimatejarvis.models import ModelGateway
from ultimatejarvis.planner import PlanValidationError, create_plan
from ultimatejarvis.windows_tools import build_read_only_registry

class FakeProvider:
    name = "fake-local"
    model = "fake"
    is_local = True
    def __init__(self, output):
        self.output = output
        self.prompt = ""
    def generate(self, prompt, *, timeout_seconds, max_output_tokens):
        self.prompt = prompt
        return self.output

class PlannerTests(unittest.TestCase):
    def test_creates_preview_plan_without_running_tools(self):
        provider = FakeProvider(json.dumps({"goal":"Inspect notes","steps":[
            {"tool":"workspace.read_text","arguments":{"relative_path":"notes.txt"},
             "reason":"Read the requested workspace note"}]}))
        plan = create_plan("Read notes.txt", ModelGateway([provider]), build_read_only_registry("."))
        self.assertEqual(plan.steps[0].tool, "workspace.read_text")
        self.assertEqual(plan.steps[0].risk, "read_only")
        self.assertIn("Do not claim actions were performed", provider.prompt)

    def test_rejects_unknown_tool(self):
        output = json.dumps({"goal":"bad","steps":[{"tool":"system.run_shell",
            "arguments":{"command":"whoami"},"reason":"run command"}]})
        with self.assertRaises(PlanValidationError):
            create_plan("run something", ModelGateway([FakeProvider(output)]), build_read_only_registry("."))

    def test_rejects_extra_arguments_and_invalid_json(self):
        output = json.dumps({"goal":"bad","steps":[{"tool":"system.diagnostics",
            "arguments":{"path":"C:/Windows"},"reason":"read diagnostics"}]})
        with self.assertRaises(PlanValidationError):
            create_plan("inspect", ModelGateway([FakeProvider(output)]), build_read_only_registry("."))
        with self.assertRaises(PlanValidationError):
            create_plan("inspect", ModelGateway([FakeProvider("not json")]), build_read_only_registry("."))

    def test_rejects_fenced_json_and_empty_steps(self):
        output = "FENCED_JSON " + json.dumps({"goal":"x","steps":[]})
        with self.assertRaises(PlanValidationError):
            create_plan("inspect", ModelGateway([FakeProvider(output)]), build_read_only_registry("."))

if __name__ == "__main__":
    unittest.main()
