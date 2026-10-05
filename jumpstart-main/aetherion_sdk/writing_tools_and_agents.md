# Write Tools and Agents

Import from the SDK's public surface:

```python
from aetherion_sdk import tool, agent, toolExecutor
```

**Use utilities from `common_lib` and `agent_lib` as needed:**

| Library | Examples | Reference |
|---------|----------|-----------|
| `common_lib` | [setup_logger](../common_lib/logger.md), [storage](../common_lib/storage.md), [database](../common_lib/database.md), [embeddings](../common_lib/embeddings.md), etc. | [Common Library](../common_lib/index.md) |
| `agent_lib` | [LLM integrations](../agent_lib/llm_factory.md), [memory](../agent_lib/memory_client.md), [file utilities](../agent_lib/file_utilities.md), etc. | [Agent Library](../agent_lib/index.md) |

---

## Basic Concepts

Building an agent involves two main components:
1. **Tools**: Functions that perform specific tasks (decorated with `@tool`).
2. **Agents**: Workflows that orchestrate tools and logic (decorated with `@agent`).

### Hello World Example

Here is a simple example of an agent that uses a tool to say hello.

```python
from aetherion_sdk import agent, tool, toolExecutor

# 1. Define a tool
@tool()
def hello_tool(name: str) -> str:
    """A simple tool that returns a greeting."""
    return f"Hello, {name}!"

# 2. Define an agent
@agent()
async def HelloAgent(name: str) -> str:
    """An agent that calls the hello_tool."""
    # Execute the tool by name
    result = await toolExecutor.execute("hello_tool", name)
    return result
```

---

## Working with File Inputs

If your tool or agent needs to process files as input, follow the below steps:

### Step 1: Upload the File to MinIO Bucket

Before running any workflow, you must upload your input file to MinIO.

**1. Create the bucket (only once)**

> ⚠️ **Important:** Your bucket must be named exactly your `team_id`.

- Open the MinIO Console
- Go to **Buckets → Create Bucket**
- Enter your `team_id` as the bucket name
- Click **Create**

**2. Upload your file**

- Open the bucket you just created
- Click **Upload**
- Select your file and upload it

> ⚠️ **Important:** The `file_key` is the same as the `file_name` you provide when uploading.

### Step 2: Retrieve the File Using the Storage Client

Use the [`storage.retrieve()`](../common_lib/storage.md) method from `common_lib` to read the file in your tool. See [Storage Client](../common_lib/storage.md) for full details.

```python
from common_lib.storage.storage_client import storage, RetrievalMode

team_id = "team-123"
file_key = "input_document.pdf"  # file_key = file_name

# Full object (returns bytes)
data = storage.retrieve(team_id, file_key, RetrievalMode.FULL_OBJECT)

# Line by line (for text files)
for line in storage.retrieve(team_id, file_key, RetrievalMode.LINE_BY_LINE):
    process(line)

# Chunked (for large files)
for chunk in storage.retrieve(team_id, file_key, RetrievalMode.CHUNKED):
    process_chunk(chunk)
```

### Step 3: Create a Tool to Process the File

Write a tool that accepts the `file_key` (which is the file name) and processes the file:

```python
from aetherion_sdk import tool
from common_lib.storage.storage_client import storage, RetrievalMode
from common_lib.utils.logger import setup_logger

logger = setup_logger(__name__)

@tool()
def process_file(team_id: str, file_key: str) -> dict:
    """
    Process a file from MinIO storage.
    
    Args:
        team_id: The team ID (used as bucket name)
        file_key: The file key/name in the bucket
    
    Returns:
        dict: Processing result
    """
    # Initialize storage client
    storage.init_client()
    
    # Retrieve the file (file_key = file_name)
    file_data = storage.retrieve(team_id, file_key, RetrievalMode.FULL_OBJECT)
    
    logger.info(f"Retrieved file: {file_key}, size: {len(file_data)} bytes")
    
    # Process the file content
    # ... your processing logic here ...
    
    return {
        "status": "success",
        "file_key": file_key,
        "size_bytes": len(file_data)
    }
```

### Complete Example: Agent with File Processing

```python
from typing import Dict, Any
from aetherion_sdk import agent, tool, toolExecutor
from common_lib.storage.storage_client import storage, RetrievalMode
from common_lib.utils.logger import setup_logger

logger = setup_logger(__name__)

@tool()
def read_and_analyze_file(team_id: str, file_key: str) -> str:
    """Read a file from storage and return its content summary."""
    storage.init_client()
    
    # file_key IS the file_name
    file_data = storage.retrieve(team_id, file_key, RetrievalMode.FULL_OBJECT)
    
    # Example: decode if it's a text file 
    # Note  : .txt and .csv are just for reference you can proceed with any file type
    if file_key.endswith('.txt') or file_key.endswith('.csv'):
        content = file_data.decode('utf-8')
        return f"File contains {len(content)} characters, {len(content.splitlines())} lines"
    
    return f"Binary file, size: {len(file_data)} bytes"

@agent()
async def FileProcessorAgent(payload: Dict[str, Any]) -> dict:
    """Agent that processes uploaded files."""
    team_id = payload.get("team_id")


    #NOTE - use the uploaded_files as the parameter



    file_key = payload.get("uploaded_files")  # uploaded_files = file_name
    
    logger.info(f"Processing file: {file_key} from bucket: {team_id}")
    
    # Use the tool to process the file
    result = await toolExecutor.execute(
        "read_and_analyze_file",
        team_id,
        file_key
    )
    
    return {"file_key": file_key, "analysis": result}
```

**Trigger the agent:**

```bash
aetherion agent FileProcessorAgent '{"team_id": "team-123", "uploaded_files": "report.csv"}' -p hello_world
```

> 📝 **Note:** The `file_key` is simply the file name you used when uploading. There's no separate key — the file name serves as both the identifier and the key in the storage bucket.

---

## Using Agent Library for LLM Calls

You can use the `agent_lib` to easily integrate LLM capabilities into your tools.

```python
from aetherion_sdk import tool
from agent_lib.llm.llm_factory import LlmFactory
from agent_lib.llm.models import OpenAIConfig

@tool()
async def summarize_text(text: str) -> str:
    """Summarize the given text using OpenAI."""
    
    # Configure the LLM
    config = OpenAIConfig(
        model_id="gpt-4",
        api_key="your-api-key" # In production, fetch this from secrets/env
    )
    
    # Create the LLM client
    llm = LlmFactory.get_llm_connection(config)
    
    # Make a synchronous call
    response_chunk = await llm.call_llm_sync(
        references=[],
        prompt=f"Summarize the following text:\n\n{text}"
    )
    
    return response_chunk.content
```

---

## Returning Structured Results for UI Display

> ⚠️ **Important:** Make sure the output for your agent is returned in below format so that it is displayed correctly on the aetherion frontend.

When your agent or tool generates outputs that should be displayed on the UI (such as downloadable files or clickable links), use **structured results** to ensure proper rendering.

### For eg - Returning an S3 Download Link

To display a downloadable file in the UI, return a structured object with `type: "s3_download_link"`:

```python
structured_results = []

# Add a downloadable file result
structured_results.append({
    "type": "s3_download_link",  # <--- Unique flag for frontend
    "title": "Recorded Session YAML",
    "file_key": result_dict["s3_key"],  # The S3 key/path of the file
    "label": "Download YAML",
    "extension": "yaml",
})
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | str | Must be `"s3_download_link"` for the frontend to recognize it |
| `title` | str | Display title shown in the UI |
| `file_key` | str | The S3 object key (file path in the bucket) |
| `label` | str | Button/link label text |
| `extension` | str | File extension (e.g., "yaml", "pdf", "csv") |

### For Returning a Downloadable URL/Link other than s3 please follow the below guideline.


```python
# Create a markdown link
url = "https://example.com/diagram.drawio"
markdown_link = f"[View As-Is Flow: Click to Open]({url})"

# Add to structured results
structured_results.append({
    "title": "As-Is Flow Diagram",
    "link": markdown_link,
    "label": "View Diagram",
    "extension": "drawio",
})
```

| Field | Type | Description |
|-------|------|-------------|
| `title` | str | Display title shown in the UI |
| `link` | str | Markdown-formatted link `[text](url)` |
| `label` | str | Button/link label text |
| `extension` | str | File type hint (e.g., "drawio", "html", "pdf") |

### Complete Example: Agent Returning Structured Results

```python
from typing import Dict, Any, List
from aetherion_sdk import agent, tool, toolExecutor
from common_lib.storage.storage_client import storage
from common_lib.utils.logger import setup_logger

logger = setup_logger(__name__)

@agent()
async def ReportGeneratorAgent(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Agent that generates reports and returns structured results for UI."""
    team_id = payload.get("team_id")
    
    structured_results: List[Dict[str, Any]] = []
    
    # Generate and upload a YAML file
    yaml_content = "report:\n  status: complete\n  items: 42"
    storage.init_client()
    bucket, s3_key = storage.store_object(
        team_id,
        "reports/session_recording.yaml",
        yaml_content.encode(),
        "application/x-yaml"
    )
    
    # Add S3 download link to results
    structured_results.append({
        "type": "s3_download_link",
        "title": "Recorded Session YAML",
        "file_key": s3_key,
        "label": "Download YAML",
        "extension": "yaml",
    })
    
    # Add a diagram link to results
    diagram_url = "https://app.diagrams.net/?file=flow_diagram.drawio"
    markdown_link = f"[View As-Is Flow: Click to Open]({diagram_url})"
    
    structured_results.append({
        "title": "As-Is Flow Diagram",
        "link": markdown_link,
        "label": "View Diagram",
        "extension": "drawio",
    })
    
    return {
        "status": "success",
        "message": "Report generated successfully",
        "results": structured_results
    }
```

> 💡 **Tip:** The frontend will parse these structured results and render appropriate UI components (download buttons, clickable links) based on the `type` and fields provided.

---

[Next Step: Metadata and Triggers](metadata_and_triggers.md)
