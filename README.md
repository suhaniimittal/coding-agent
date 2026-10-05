# code-agent


### Creating a New Project

Use the `aetherion` CLI to scaffold a new project.

```bash

aetherion init code-agent
cd code-agent

uv sync

source .venv/bin/activate
```

## Configuration

```bash
aetherion config init
```

## Write Tools and Agents

Example `agent/metadata.json`:

```json
{
  "name": "code-agent",
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

Supported trigger fields:

- text / str  
- dropdown  
- file / form  
- boolean  
- number  
- textarea  
- default  


### Tool Execution Options

```python
from datetime import timedelta
from aetherion_sdk import toolExecutor

result = await toolExecutor.execute(
    "analyze_text",
    "Some text",
    start_to_close_timeout=timedelta(seconds=5),
)
```

## Running Workers

```bash
aetherion run --tool
```

```bash
aetherion run --agent
```

Trigger an agent:

```bash
aetherion agent code-agent '{"input": "World"}'
```

## Publish

```bash
aetherion publish
```

