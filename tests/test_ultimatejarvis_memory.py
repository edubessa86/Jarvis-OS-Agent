import tempfile
import unittest
from pathlib import Path

from ultimatejarvis.memory import LocalMemory


class LocalMemoryTests(unittest.TestCase):
    def test_remember_search_and_delete(self):
        with LocalMemory(":memory:") as memory:
            item = memory.remember(
                "Football training on Wednesday",
                category="routine",
                tags=["sports", "weekly"],
            )
            self.assertEqual(item["category"], "routine")
            self.assertIn("sports", item["tags"])
            found = memory.search("Football")
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["memory_id"], item["memory_id"])
            self.assertTrue(memory.delete(item["memory_id"]))
            self.assertEqual(memory.search("Football"), [])
            self.assertFalse(memory.delete(item["memory_id"]))

    def test_memory_persists_across_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "memory.sqlite3"
            with LocalMemory(db) as memory:
                item = memory.remember("Use local models first", tags=["ai", "privacy"])
            with LocalMemory(db) as memory:
                loaded = memory.get(item["memory_id"])
                self.assertEqual(loaded["content"], "Use local models first")
                self.assertEqual(loaded["tags"], ["ai", "privacy"])

    def test_reject_empty_or_oversized_content(self):
        with LocalMemory(":memory:", max_content_chars=10) as memory:
            with self.assertRaises(ValueError):
                memory.remember(" ")
            with self.assertRaises(ValueError):
                memory.remember("this is too long")

    def test_reject_invalid_search_and_limit(self):
        with LocalMemory(":memory:") as memory:
            with self.assertRaises(ValueError):
                memory.search(" ")
            with self.assertRaises(ValueError):
                memory.search("word", limit=0)

    def test_fts_special_characters_do_not_crash_search(self):
        with LocalMemory(":memory:") as memory:
            memory.remember('Jarvis supports "local-first" models')
            result = memory.search('"local-first"')
            self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
