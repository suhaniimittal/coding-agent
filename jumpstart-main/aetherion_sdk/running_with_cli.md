# Running with the CLI

## Verify if your scaffolded code is working with the CLI

Discovery requires your package to be provided via `-p/--package`. Use **separate terminals** for workers.

> **NOTE:** Before creating/splitting a new terminal, make sure you are sourcing the venv

```bash
# Start the tool worker:
aetherion run --tool 

# Start the agent worker:
aetherion run --agent

# Trigger an agent by name (payload must be a JSON object string):
aetherion agent HelloAgent '{"input": "World"}'
```

---

[Next Step: Configuration](configuration.md)
