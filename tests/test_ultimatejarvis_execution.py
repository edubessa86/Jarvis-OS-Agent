import json
import tempfile
import unittest
from pathlib import Path
from ultimatejarvis.audit import SQLiteToolAuditLog
from ultimatejarvis.execution import execute_approved_plan
from ultimatejarvis.planner import PlannedStep, ToolPlan
from ultimatejarvis.windows_tools import build_read_only_registry

class ApprovalExecutionTests(unittest.TestCase):
    def test_declined_step_is_not_invoked_and_is_audited(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"notes.txt").write_text("private contents",encoding="utf-8")
            registry=build_read_only_registry(root)
            plan=ToolPlan("read note",(PlannedStep("workspace.read_text",{"relative_path":"notes.txt"},"inspect note","read_only"),),"fake","test")
            with SQLiteToolAuditLog(root/"audit.sqlite3") as log:
                results=execute_approved_plan(plan,registry,approve_step=lambda *args:False,audit=log.append)
                self.assertEqual(results[0].status,"rejected_by_user")
                self.assertEqual(log.recent()[0]["status"],"rejected_by_user")
                self.assertNotIn("private contents",str(log.recent()))

    def test_approved_step_returns_value_but_audit_omits_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"notes.txt").write_text("hello",encoding="utf-8")
            registry=build_read_only_registry(root)
            plan=ToolPlan("read note",(PlannedStep("workspace.read_text",{"relative_path":"notes.txt"},"read","read_only"),),"fake","test")
            with SQLiteToolAuditLog(root/"audit.sqlite3") as log:
                results=execute_approved_plan(plan,registry,approve_step=lambda *args:True,audit=log.append)
                self.assertEqual(results[0].status,"succeeded")
                self.assertEqual(results[0].value["content"],"hello")
                self.assertNotIn("hello",str(log.recent()))

    def test_revalidates_all_steps_before_asking_approval(self):
        registry=build_read_only_registry(".")
        plan=ToolPlan("bad",(PlannedStep("system.run_shell",{"command":"whoami"},"run","read_only"),),"fake","test")
        prompted=[]
        with self.assertRaises(ValueError):
            execute_approved_plan(plan,registry,approve_step=lambda *args:prompted.append(args) or True,audit=lambda event:None)
        self.assertEqual(prompted,[])

if __name__=="__main__":
    unittest.main()
