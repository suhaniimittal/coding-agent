# Aetherion SDK

The `aetherion_sdk` package is the developer surface for building agents and tools on the Aetherion platform. It bundles the decorators, the runtime, the workers, and the `aetherion` CLI into one import.

| Concept | Description |
|---------|-------------|
| **Tools** | Annotated with `@tool()` — Python functions you want the platform to run (API calls, file processing, LLM calls, etc.). |
| **Agents** | Annotated with `@agent()` — workflows that orchestrate tools and make decisions. |
| **Utilities** | Available from `common_lib` and `agent_lib` — see [Common Library](../common_lib/index.md) and [Agent Library](../agent_lib/index.md). |

## Documentation Contents

- [Getting Started](getting_started.md)
- [Running with the CLI](running_with_cli.md)
- [Writing Tools and Agents](writing_tools_and_agents.md)
- [Decorators & Executors Reference](decorators_executors.md)
- [Metadata and Triggers](metadata_and_triggers.md)
- [Configuration](configuration.md)
- [Publish](publish.md)
- [CLI Reference](cli_reference.md)
- [Troubleshooting](troubleshooting.md)
