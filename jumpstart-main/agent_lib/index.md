# Agent Library

The `agent_lib` package provides LLM provider integrations, memory management, and file-reference utilities for use inside agents and tools.

| Component | Description |
|-----------|-------------|
| **[LLM Factory](llm_factory.md)** | Pick a provider from a `model_id` and return an LLM client. |
| **[LLM Calls](llm_calls.md)** | The abstract interface implemented by every provider. |
| **[OpenAI](openai.md)** | GPT and OpenAI-compatible models. |
| **[Anthropic](anthropic.md)** | Claude models. |
| **[Bedrock](bedrock.md)** | AWS Bedrock-hosted models. |
| **[Gemini](gemini.md)** | Google Gemini models. |
| **[Memory Client](memory_client.md)** | Persistent memory via Mem0 (sync and async). |
| **[File Utilities](file_utilities.md)** | Fetch and extract text from PDFs, DOCX, CSV, Excel, HTML, Markdown. |
