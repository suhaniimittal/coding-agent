# Configuration

> **Note:** When running `aetherion init` for the first time, `aetherion config init` is called for you. You'll be prompted to enter your `CLIENT_ID` and `CLIENT_SECRET`, and (optionally) the path to a `.env` file to source.

Settings are read in this order of precedence (highest wins):

1. Environment variables
2. User config file `~/.config/aetherion/config.json` (managed via `aetherion config`)
3. Project `.env` in the current working directory
4. Library defaults

## Environment Variables

**Core runtime:**

| Variable | Default | Description |
|----------|---------|-------------|
| `AETHERION_TARGET_HOST` | `localhost:7233` | Workflow server `host:port`. |
| `AETHERION_NAMESPACE` | `default` | Namespace to target. |
| `AETHERION_AGENT_TASK_QUEUE` | `aetherion-workflows` | Agent worker queue. |
| `AETHERION_TOOL_TASK_QUEUE` | `aetherion-activities` | Tool worker queue. |
| `AETHERION_AGENT_TASK_QUEUE_MAP` | `{}` | JSON map of agent name → custom queue. |
| `AETHERION_TOOL_TASK_QUEUE_MAP` | `{}` | JSON map of tool name → custom queue. |
| `AETHERION_PACKAGES` | _empty_ | Comma-separated packages to auto-import for tool/agent discovery. |
| `AETHERION_AUTO_DISCOVER` | `true` | Auto-import agent/tool modules at worker startup. |
| `AETHERION_LOG_LEVEL` | `INFO` | Worker log level. |

**Authentication and publish:**

| Variable | Default | Description |
|----------|---------|-------------|
| `AETHERION_CLIENT_ID` | `aetherion-client` | OAuth Client ID. |
| `AETHERION_CLIENT_SECRET` | _required for publish_ | OAuth Client Secret. |
| `AETHERION_REALM_URL` | `http://localhost:53585/realms/demo` | Identity provider realm URL. |
| `AETHERION_API_BASE_URL` | `http://localhost:8010/api/v1` | Platform API base URL. |
| `AETHERION_TOKEN_REFRESH_BUFFER` | `30` | Seconds before token expiry to refresh. |
| `REPO_URL` | _required for publish_ | Artifact repository URL used when publishing. |

## Configure via CLI (Recommended)

Use the built-in config commands to create and manage `~/.config/aetherion/config.json`. **Keys in the config file are uppercase** (the CLI uppercases whatever you type); the equivalent environment variables use the `AETHERION_*` names from the table above.

```bash
# Guided setup (creates/updates the config file)
aetherion config init

# Show current config
aetherion config list

# Set individual values
aetherion config set TARGET_HOST localhost:7233
aetherion config set REALM_URL http://localhost:53585/realms/demo
aetherion config set API_BASE_URL http://localhost:8001/api/v1
aetherion config set NAMESPACE default

# Import all keys from a .env file
aetherion config set env ./.env
```

## Precedence Recap

Env vars > user config > project `.env` > defaults

> ⚠️ **If your `.env` values are not being loaded, source them explicitly:**
> ```bash
> set -a
> source "PATH_TO_.env_FILE"
> set +a
> ```

---

[Next Step: Writing Tools & Agents](writing_tools_and_agents.md)
