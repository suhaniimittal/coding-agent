# 🚀 Aetherion Jumpstart Guide

Welcome to the **Aetherion Jumpstart**! This guide will help you set up your environment, install the SDK, and build your first Aetherion Agent.

> [!NOTE]
> For detailed information, navigate to the [Detailed Topics](#detailed-topics) section at the bottom of this page.

---

<a id="getting-started"></a>
# Getting Started

## 📋 Prerequisites

Ensure your system meets the following requirements before proceeding.

### 🐍 Python 3.12
Aetherion requires Python 3.12. Install it via Homebrew:

```bash
brew install python@3.12
```

### 🔧 Virtual Environment
Create and activate an isolated Python environment to manage dependencies cleanly:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```
---

## 🛠️ Installation & Configuration

### 1. Install Aetherion SDK CLI
Run the installation script to get the latest version of the CLI:

```bash
/bin/bash -c "$(curl -fsSL https://sdk.sbox.aetherion.io/install.sh)"
```

### 2. Configure the CLI
Initialize the CLI with your credentials.

> [!IMPORTANT]
> **Credentials Required**
> You will need your **Client ID** and **Client Secret** provided by your Aetherion administrator.

```bash
aetherion config init
```

---

## ⚡ Quick Start

### 1. Explore Commands
Check available commands to ensure everything is set up correctly:

```bash
aetherion --help
```

### 2. Initialize a New Project
Create a new agent project named `my_first_agent`:

```bash
aetherion init my_first_agent
```

### 3. Install dependancies

```bash
cd my_first_agent
``` 

```bash
uv sync
```

### 4. Write Your Tools and Agents
The scaffold ships with a Hello-World example so you can verify the round-trip immediately. To build your own logic, see the full guide: **[Writing Tools & Agents](aetherion_sdk/writing_tools_and_agents.md)**.

### 5. Run the Agent
Run the **Tool Worker** and **Agent Worker** in separate terminal windows:

**Start the Tool Worker:**
```bash
aetherion run --tool
```

**Start the Agent Worker:**
```bash
aetherion run --agent
```

### 6. Interact with the Agent
Send a test message to your running agent to verify it's working:

```bash
aetherion agent my_first_agent '{"input": "John"}' 
```

### 7. Publish to the Platform
Once the agent runs cleanly locally, ship it. From the project root, build a single combined package (agent + tools) and upload it to the platform:

```bash
aetherion publish
```

> ⚠️ Bump the project version in `pyproject.toml` before each republish. See [Publish](aetherion_sdk/publish.md) for the full flow.

---

## 💻 Developing Agents

Aetherion agents are built using the Python SDK. Define **Tools** for any code that touches the outside world (HTTP, files, LLMs), and **Agents** that orchestrate tools into a workflow.

### Example: Hello World Agent

```python
from aetherion_sdk import agent, tool, toolExecutor

@tool()
def hello_tool(name: str) -> str:
    return f"Hello, {name}!"

@agent()
async def HelloAgent(name: str) -> str:
    return await toolExecutor.execute("hello_tool", name)
```

For comprehensive guides and advanced examples, see **[Writing Tools & Agents](aetherion_sdk/writing_tools_and_agents.md)**.

---

## 🧩 What's in the SDK

The Aetherion SDK is a single product made up of a few importable packages. Pick the ones you need.

| Package | What it gives you |
|---------|-------------------|
| [`aetherion_sdk`](aetherion_sdk/index.md) | Decorators (`@tool`, `@agent`), the runtime, the CLI, and workers |
| [`common_lib`](common_lib/index.md) | Database, S3-compatible storage, logging, embeddings, vector stores, agent/activity execution helpers, Redis payload codec |
| [`agent_lib`](agent_lib/index.md) | LLM providers (OpenAI, Anthropic, Bedrock, Gemini), memory client, file reference utilities |
| [`scraper`](screper/index.md) | Declarative, YAML-driven web scraping with Playwright and Steel backends |
| [`a2a_lib`](a2alib/index.md) | Agent-to-agent communication protocol — typed request/response models and helpers |

---

## 🎯 End-to-End Quickstart

A minimal project that uses `aetherion_sdk` + `common_lib` + the `scraper` package together. Drop these two files into a project scaffolded by `aetherion init my_agent`.

**`my_agent/tools/tools.py`**

```python
from aetherion_sdk import tool
from common_lib.storage.storage_client import storage, RetrievalMode
from common_lib.utils.logger import setup_logger
from scraper.scraper_factory import ScraperFactory
from scraper.scraper_config import ScraperConfig

logger = setup_logger(__name__)


@tool()
def read_input_file(team_id: str, file_key: str) -> str:
    """Read a file uploaded to the team's storage bucket."""
    storage.init_client()
    data = storage.retrieve(team_id, file_key, RetrievalMode.FULL_OBJECT)
    return data.decode("utf-8")


@tool()
def scrape_page(url: str) -> dict:
    """Scrape a page and return its <h1> text."""
    config = ScraperConfig.from_dict({
        "scraper": {"type": "playwright", "headless": "true"},
        "actions": [
            {"type": "navigate", "args": {"url": url}},
            {"type": "extract_text", "args": {"selector": "h1", "variable_name": "title"}},
        ],
    })
    scraper = ScraperFactory.get_scraper("playwright", config)
    scraper.run()
    return {"title": getattr(config, "title", None)}
```

**`my_agent/agent/agent.py`**

```python
from typing import Dict, Any
from aetherion_sdk import agent, toolExecutor


@agent()
async def MyAgent(payload: Dict[str, Any]) -> dict:
    team_id = payload["team_id"]
    file_key = payload["file_key"]
    url = payload["url"]

    file_text = await toolExecutor.execute("read_input_file", team_id, file_key)
    scrape_result = await toolExecutor.execute("scrape_page", url)

    return {
        "file_chars": len(file_text),
        "scraped_title": scrape_result["title"],
    }
```

**Run it** (three terminals, all inside the project directory):

```bash
# Terminal 1
aetherion run --tool

# Terminal 2
aetherion run --agent

# Terminal 3
aetherion agent MyAgent '{"team_id":"team-123","file_key":"input.txt","url":"https://example.com"}'
```

---

<a id="detailed-topics"></a>
## 📚 Detailed Topics

- [Getting Started](aetherion_sdk/getting_started.md)
- [Aetherion SDK](aetherion_sdk/index.md)
    - [CLI Configuration](aetherion_sdk/configuration.md)
    - [CLI Reference](aetherion_sdk/cli_reference.md)
    - [Writing Tools & Agents](aetherion_sdk/writing_tools_and_agents.md)
    - [Decorators & Executors](aetherion_sdk/decorators_executors.md)
    - [Metadata and Triggers](aetherion_sdk/metadata_and_triggers.md)
- Reference packages:
    - [Common Library (`common_lib`)](common_lib/index.md)
    - [Agent Library (`agent_lib`)](agent_lib/index.md)
    - [Scraper (`scraper`)](screper/index.md)
    - [Agent-to-Agent (`a2a_lib`)](a2alib/index.md)


---

[Next Step: Setup](aetherion_sdk/getting_started.md)
