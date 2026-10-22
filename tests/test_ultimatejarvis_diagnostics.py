import unittest

from ultimatejarvis.diagnostics import collect_diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_reports_runtime_without_gpu_or_model_probing(self):
        result = collect_diagnostics()
        self.assertIn("os", result)
        self.assertIn("python_version", result)
        self.assertIn("logical_cpu_count", result)
        self.assertEqual(result["gpu"], "not probed")
        self.assertEqual(result["model_runtime"], "not probed")

    def test_ram_is_optional_and_non_negative(self):
        result = collect_diagnostics()
        if result["total_ram_bytes"] is not None:
            self.assertGreater(result["total_ram_bytes"], 0)
        if result["total_ram_gib"] is not None:
            self.assertGreater(result["total_ram_gib"], 0)


if __name__ == "__main__":
    unittest.main()
