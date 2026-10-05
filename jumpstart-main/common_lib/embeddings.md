# Embeddings

**Module:** `common_lib.embeddings`

Generate text embeddings (via OpenAI) for chunks of content, and process common file types through built-in extractors.

## Generate Embeddings

```python
from common_lib.embeddings.embeddings_generator import EmbeddingsGenerator
from common_lib.models.embeddings import EmbeddingsLlmConfig, Reference

config = EmbeddingsLlmConfig(
    model="text-embedding-ada-002",
    api_key="sk-...",
    dimensions=1536,
)
generator = EmbeddingsGenerator(config)

embeddings = generator.generate_embeddings(
    Reference(agent="test", filename="doc.pdf"),
    ["chunk 1", "chunk 2", "chunk 3"],
)
```

## File-Type Processors

`EmbeddingProcessorFactory` picks the right extractor based on the file extension.

| Method | Description |
|--------|-------------|
| `EmbeddingProcessorFactory.create_processor(generic_embedding_processor)` | Create a processor for the file in `generic_embedding_processor`. |
| `EmbeddingProcessorFactory.register_processor(file_extension, processor_class)` | Plug in a custom processor. |
| `EmbeddingProcessorFactory.get_supported_extensions()` | List all supported file extensions. |

**Supported extensions:** `.pdf`, `.txt`, `.md`, `.html`, `.htm`, `.csv`, `.docx`, `.doc`, `.xlsx`, `.xls`, `.json`.

```python
from common_lib.embeddings.embedding_processor_factory import EmbeddingProcessorFactory
from common_lib.models.embeddings import GenericEmbeddingProcessor, EmbeddingsLlmConfig
from common_lib.models.vectors import PostgresStorage

processor = EmbeddingProcessorFactory.create_processor(
    GenericEmbeddingProcessor(
        customer="customer1",
        filename="document.pdf",
        embeddings_generator=EmbeddingsLlmConfig(
            model="text-embedding-ada-002",
            api_key="sk-...",
            dimensions=1536,
        ),
        storageConfig=PostgresStorage(
            customer="customer1",
            collection_name="embeddings",
        ),
        max_chunk_len=800,
        metadata={"source": "upload"},
    )
)
result = processor.process_and_store_embeddings()
```
