# UltimateJarvis — Local Memory

The optional `LocalMemory` component stores notes in a local SQLite database. It uses FTS5 when the installed SQLite build supports it, with a parameterized `LIKE` fallback. No embedding model, vector database, or network connection is required.

## Example

```python
from ultimatejarvis.memory import LocalMemory

with LocalMemory(r"C:\Users\YOUR_USER\AppData\Local\UltimateJarvis\memory.sqlite3") as memory:
    saved = memory.remember(
        "Prefer local AI models when practical",
        category="preferences",
        tags=["ai", "privacy"],
    )
    matches = memory.search("local AI")
    print(matches)
```

Memory is explicit: the module does not silently capture chats, scan user files, or upload records. Data is stored as plaintext in SQLite; use a private directory and do not store passwords, tokens, or secrets. Encryption, retention policies, automatic summarization, and integration with the legacy agent are not included yet.

## Tests

```powershell
py -3 -m unittest discover -s tests -p "test_ultimatejarvis_*.py" -v
```
