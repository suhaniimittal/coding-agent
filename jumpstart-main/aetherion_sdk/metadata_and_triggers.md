# Metadata and Triggers

> ⚠️ **Important:**  In the Trigger section, make sure the type is correctly specified. This value determines the agent’s expected input format, and the frontend relies on it to generate the appropriate UI fields automatically 

Each agent and tool package includes a `metadata.json` file. This file defines package details and input triggers (for agents) that the frontend parses to generate dynamic UI forms.

> ⚠️ **Important:** Version bumps for republishing live in `pyproject.toml`, not in `metadata.json`. Update the `version` field in `pyproject.toml` before running `aetherion publish`.


**Example `agent/metadata.json`:**

```json
{
  "name": "hello_world",
  "version": "1.0.0",
  "description": "Agent workflows for My Package.",
  "config": {
    "triggers": [
      {
        "name": "files",
        "type": "file",
        "required": true,
        "description": "Files to be uploaded for processing.",
        "friendly_name": "Upload Files"
      }
    ]
  }
}
```

**Supported Values for Trigger "Type":**

| Type | Description |
|------|-------------|
| `text` / `str` | Single-line text input |
| `dropdown` | Selection input; supports "label:val,…" format in description or comma-separated options |
| `file` / `form` | File upload input |
| `boolean` / `bool` | Yes/No toggle |
| `number` / `int` / `float` | Numeric input |
| `textarea` / `longtext` | Multi-line text input |
| `default` | Text input with type name as placeholder |

**Trigger Properties:** `name`, `type`, `required`, `description`, `friendly_name`, `accepts` (for files)

---

[Next Step: Decorators & Executors](decorators_executors.md)
