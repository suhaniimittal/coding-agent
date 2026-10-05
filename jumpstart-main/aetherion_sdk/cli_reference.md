# CLI Reference

This document is the complete reference for the `aetherion` CLI.

## Global Options

| Option | Description |
|--------|-------------|
| `--help` | Show the help message and exit. |

## Commands

| Command | Purpose |
| :--- | :--- |
| [`version`](#version) | Print the SDK version. |
| [`init`](#init) | Scaffold a new Aetherion project. |
| [`run`](#run) | Start the agent and/or tool worker. |
| [`agent`](#agent) | Execute a specific agent by name with a JSON payload. |
| [`publish`](#publish) | Build and upload agent and tool artifacts to the platform. |
| [`config`](#config) | Manage CLI configuration. |
| [`pull`](#pull) | Pull skills from a GitHub release. |

### `version`

Print the version of the Aetherion SDK.

```bash
aetherion version
```

---

### `init`

Create a new Aetherion starter project.

**Usage:**

```bash
aetherion init <PROJECT_NAME> [OPTIONS]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `<PROJECT_NAME>` | Name of the project to create. |

**Options:**

| Option | Description |
|--------|-------------|
| `-p, --path <PATH>` | Directory where the project should be created (default: current directory). |
| `--tool` | Scaffold a tool-only project (no agent). |

**Example:**

```bash
aetherion init my-agent-project
```

---

### `run`

Run the agent and/or tool worker.

**Usage:**

```bash
aetherion run [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--agent` | Run only the agent worker. |
| `--tool` | Run only the tool worker. |

*If neither flag is passed, both workers start in the same process. You cannot specify both flags at once — pick one per terminal.*

**Examples:**

```bash
# Run both workers in one process
aetherion run

# Run only the agent worker (separate terminals recommended in development)
aetherion run --agent
```

---

### `agent`

Start an agent by name with a JSON payload — useful for testing or manual triggers.

**Usage:**

```bash
aetherion agent <AGENT_NAME> <PAYLOAD> [OPTIONS]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `<AGENT_NAME>` | Registered agent name (e.g., `HelloAgent` or `my_package.agent.HelloAgent`). |
| `<PAYLOAD>` | JSON object string passed to the agent. |

**Options:**

| Option | Description |
|--------|-------------|
| `--id <ID>` | Custom agent/workflow ID (auto-generated if not provided). |
| `--task-queue <QUEUE>` | Task queue override. |
| `--wait / --no-wait` | Wait for the workflow result before exiting (default: `--wait`). |

**Examples:**

```bash
# Start an agent and wait for the result
aetherion agent hello_world.agent.HelloAgent '{"input": "user123"}'

# Fire-and-forget
aetherion agent hello_world.agent.HelloAgent '{"input": "user123"}' --no-wait

# Custom workflow ID
aetherion agent HelloAgent '{"input": "val"}' --id my-custom-id
```

---

### `publish`

Build a single combined package (agent + tools) from your project and upload it to the platform for registration. Must be run from the project root (the directory containing `src/`).

**Usage:**

```bash
aetherion publish [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--source <DIR>` | `.` | Root directory of the project. |
| `--dist <DIR>` | `dist` | Output directory for the temporary build artifact. |

*Requires successful authentication via the token manager. There is no separate agent-only or tool-only mode — every publish builds and uploads agent + tools together as one artifact.*

**Example:**

```bash
aetherion publish
aetherion publish --source . --dist build
```

> ⚠️ **Bump the project version in `pyproject.toml` before republishing** so the platform can track the new release.

See [Publish](publish.md) for the full flow and what gets included in the artifact.

---

### `config`

Manage CLI configuration. The CLI reads settings from environment variables, then `~/.config/aetherion/config.json`, then the project `.env`, then library defaults.

#### `config list`

List the current configuration.

```bash
aetherion config list
```

#### `config init`

Run the interactive setup wizard. Creates or updates `~/.config/aetherion/config.json`.

```bash
aetherion config init
```

The wizard prompts you for:

| Prompt | Stored as | Notes |
|--------|-----------|-------|
| Client ID | `CLIENT_ID` | OAuth Client ID from your Aetherion administrator. |
| Client Secret | `CLIENT_SECRET` | OAuth Client Secret. |
| Realm | _(used to derive URLs)_ | Your tenant realm name. |

The wizard then auto-derives and saves:

- `TARGET_HOST` — workflow server `host:port`
- `REALM_URL` — identity provider URL for the given realm
- `API_BASE_URL` — platform API URL for the given realm
- `NAMESPACE` — defaults to `default`
- Local storage defaults (`STORAGE_ENDPOINT`, `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY`, …)

At the end it offers to import additional variables from a `.env` file.

#### `config set`

Set a single configuration value. Keys are stored **uppercase** — the CLI normalises whatever case you type.

**Usage:**

```bash
aetherion config set <KEY> <VALUE>
```

**Special usage:**

```bash
# Import every key from a .env file
aetherion config set env <FILE_PATH>
```

**Standard keys:**

| Key | Description |
|-----|-------------|
| `TARGET_HOST` | Workflow server `host:port`. |
| `CLIENT_ID` | OAuth Client ID. |
| `CLIENT_SECRET` | OAuth Client Secret. |
| `REALM_URL` | Identity provider realm URL. |
| `API_BASE_URL` | Platform API base URL. |
| `NAMESPACE` | Namespace to target. |

Non-standard keys are still accepted — the CLI stores them with a warning, which is useful for project-specific values (e.g. `STORAGE_ENDPOINT`).

**Example:**

```bash
aetherion config set NAMESPACE production
aetherion config set TARGET_HOST localhost:7233
aetherion config set env ./.env
```

---

### `pull`

Pull a release of agent skills from a GitHub repository into your local skills directory.

**Usage:**

```bash
aetherion pull [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--skills [AGENT_NAME]` | Pull the skills archive; optionally with an agent name for context. |

**Environment variables:**

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub Personal Access Token (required). |
| `SKILLS_GITHUB_REPO` | GitHub repository slug (`org/repo`) hosting the release. |
| `SKILLS_RELEASE_VERSION` | Release tag to pull. |
| `SKILLS_DIRECTORY` | Destination on disk (default: `./skills`). |
| `AGENT_NAME` | Fallback agent name if not passed on the CLI. |

**Example:**

```bash
GITHUB_TOKEN=ghp_xxx SKILLS_GITHUB_REPO=my-org/my-skills \
SKILLS_RELEASE_VERSION=v1.2.0 aetherion pull --skills MyAgent
```

---

## Configuration

> **Note:** When running `aetherion init` for the first time, `aetherion config init` is called for you. You'll be prompted for `CLIENT_ID` and `CLIENT_SECRET`, and (optionally) a path to a `.env` file to source.

Settings are read in this order of precedence (highest first):

1. Environment variables
2. User config file `~/.config/aetherion/config.json`
3. Project `.env` in the current working directory
4. Library defaults

**Common environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `AETHERION_TARGET_HOST` | `localhost:7233` | Workflow server `host:port`. |
| `AETHERION_NAMESPACE` | `default` | Namespace to target. |
| `AETHERION_AGENT_TASK_QUEUE` | `aetherion-workflows` | Agent worker queue. |
| `AETHERION_TOOL_TASK_QUEUE` | `aetherion-activities` | Tool worker queue. |
| `AETHERION_AGENT_TASK_QUEUE_MAP` | `{}` | JSON map of agent name → custom queue. |
| `AETHERION_TOOL_TASK_QUEUE_MAP` | `{}` | JSON map of tool name → custom queue. |
| `AETHERION_PACKAGES` | _empty_ | Comma-separated packages to auto-import. |
| `AETHERION_AUTO_DISCOVER` | `true` | Auto-import agent/tool modules at boot. |
| `AETHERION_LOG_LEVEL` | `INFO` | Worker log level. |

**Auth and API for publish:**

| Variable | Description |
|----------|-------------|
| `AETHERION_CLIENT_ID` | OAuth Client ID. |
| `AETHERION_CLIENT_SECRET` | OAuth Client Secret. |
| `AETHERION_REALM_URL` | Identity provider realm URL. |
| `AETHERION_API_BASE_URL` | Platform API base URL. |
| `AETHERION_TOKEN_REFRESH_BUFFER` | Seconds before token expiry to refresh (default `30`). |

**Recommended: configure via CLI.** Config-file keys are uppercase; environment-variable equivalents are the `AETHERION_*` names above.

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
```

> ⚠️ **If your `.env` values are not being loaded, source them explicitly:**
> ```bash
> set -a
> source "PATH_TO_.env_FILE"
> set +a
> ```
