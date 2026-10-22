# UltimateJarvis — Local CLI

Run commands from the repository root with Python 3.11 or newer.

## Diagnostics

```powershell
py -3 -m ultimatejarvis diagnostics
py -3 -m ultimatejarvis diagnostics --workspace "$env:USERPROFILE\UltimateJarvisWorkspace"
```

## Ask the local Ollama model

Start Ollama and make sure the chosen model is installed first:

```powershell
ollama pull qwen2.5:3b
py -3 -m ultimatejarvis ask --model qwen2.5:3b "Explique o que é o UltimateJarvis em português."
```

The command only sends the supplied prompt to the local Ollama adapter. It does not automatically append saved memories, read arbitrary files, or execute the model's response.

## Explicit local memory

```powershell
py -3 -m ultimatejarvis memory add "Preferir modelos locais quando possível" --category preferencias --tag ia --tag privacidade
py -3 -m ultimatejarvis memory search "modelos locais"
py -3 -m ultimatejarvis memory list
```

The default database path is under the current user's local application-data directory. Override it with `--db` before the subcommand:

```powershell
py -3 -m ultimatejarvis memory --db "$env:LOCALAPPDATA\UltimateJarvis\test.sqlite3" add "Nota de teste"
```

Delete a note using its `memory_id` from the JSON output:

```powershell
py -3 -m ultimatejarvis memory delete ID_DA_MEMORIA
```

Memory is plaintext in SQLite and remains on the machine. Do not store passwords, API keys or other secrets.
