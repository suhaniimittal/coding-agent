# LLM Calls

**Module:** `agent_lib.llm.llm_calls`

`LlmCalls` is the abstract base class every provider implementation follows. You normally don't instantiate it directly — call `LlmFactory.get_llm_connection(config)` to get a concrete client.

| Method | Description | Returns |
|--------|-------------|---------|
| `call_llm(reference, prompt)` | Streaming LLM call. Iterates response chunks; async on OpenAI, sync iterator on the others. | `Iterator[BaseMessageChunk]` |
| `call_llm_sync(reference, prompt)` | Synchronous (blocking) LLM call. | `Iterator[BaseMessageChunk]` |

`reference` is a `common_lib.models.embeddings.Reference` — pointers to documents the LLM should consider as context. Providers automatically fetch the referenced content and prepend it to the prompt.
