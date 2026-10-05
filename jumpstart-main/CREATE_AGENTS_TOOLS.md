# How to Create Agents & Tools

This guide covers the steps to initialize and create new Aetherion Agents and Tools.

## 🚀 Initialize a New Project

To create a new project that can contain agents and tools, use the `init` command followed by your desired project name:

```bash
aetherion init my_first_agent
```

This will create a new directory with the basic structure for your project.

---

## 🤖 Creating Agents

Agents are functions decorated with `@agent()`. They accept a dictionary as input and must return a dictionary.

> [!IMPORTANT]
> **Execution Constraints**
> The Agent logic must be **deterministic** and **self-contained**.
> - **No Direct I/O:** Do not make network requests, database connections, or file operations directly in the agent. All external side effects must be performed by calling **Tools**.
> - **Deterministic Execution:** Avoid logic that produces variable results (e.g., `random`, `datetime.now()`, threading/globals) to ensure the agent's state can be reliably reconstructed.

### Example Agent

```python
from __future__ import annotations
from typing import Dict, Any
from aetherion_sdk import agent, toolExecutor

@agent()
async def my_agent(payload: Dict[str, Any]) -> dict:
    # Agent logic here
    # Use toolExecutor to call tools
    tool_result = await toolExecutor.execute("my_tool", "some input")
    return {"status": "success", "data": tool_result}
```

### Executing Tools
Agents interact with the outside world by executing tools using `toolExecutor.execute()`:

```python
from aetherion_sdk import toolExecutor

# Execute a tool by name with input
tool_result = await toolExecutor.execute("tool_name", input_data)
```

---

## 🛠️ Creating Tools

Tools are functions decorated with `@tool()`. They can accept any valid Python input and produce any valid Python output.

> [!IMPORTANT]
> **Unique Names Required**
> Each tool must have a unique name across your project.

### Example Tool

```python
from __future__ import annotations
from typing import Dict, Any
from aetherion_sdk import tool

@tool()
async def my_tool(input_data: str) -> dict:
    # Perform external I/O or complex logic here
    return {"processed": input_data}
```
