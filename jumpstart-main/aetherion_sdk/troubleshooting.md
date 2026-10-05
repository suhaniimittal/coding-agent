# Troubleshooting

| Issue | Solution |
|-------|----------|
| "No tools/agents registered" | Ensure you passed `-p my_agent` to the CLI or set `AETHERION_PACKAGES=my_agent`. |
| Workers not receiving tasks | Verify your task queues align between settings and workers (`AETHERION_AGENT_TASK_QUEUE`, `AETHERION_TOOL_TASK_QUEUE`). |
| Connection issues | Confirm `AETHERION_TARGET_HOST` is correct and the server is reachable. |
| Invalid payload | The payload passed to `aetherion agent` must be a valid JSON object string. |
| Storage retrieval fails | Confirm `storage.init_client()` is called and `STORAGE_*` env vars are set; the bucket name should be your `team_id`. |
