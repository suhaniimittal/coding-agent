# Publish

`aetherion publish` builds your project — agent and tools together — into a single combined package and uploads it to the Aetherion platform for registration. It is equivalent to running workers locally, but the code lives on the remote environment.

## Usage

Run this from the **root of your project** (the directory that contains `src/`):

```bash
aetherion publish
```

That's the whole workflow. The command:

1. Detects packages under `src/` and loads your project `.env`.
2. Authenticates with the realm configured by `aetherion config init`.
3. Bundles **agent + tools as one artifact**, excluding `.venv` and stub files.
4. Uploads the artifact to `/agent/register` on the platform.
5. Cleans up the local tarball.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--source <DIR>` | `.` | Root directory of the project. |
| `--dist <DIR>` | `dist` | Output directory for the temporary build artifact. |

**Example:**

```bash
aetherion publish --source . --dist dist
```

> ⚠️ **Bump the project version in `pyproject.toml` before republishing.** The platform uses it to track new releases.

## What gets included

The packer walks your project starting at `--source/src/` and includes the real Python source for all detected packages. It explicitly excludes:

- `.venv/`
- `__pycache__/`
- Build artifacts in `dist/`

If authentication fails, the upload is aborted before any data leaves your machine. Check `AETHERION_CLIENT_ID`, `AETHERION_CLIENT_SECRET`, and `AETHERION_REALM_URL` (or your `~/.config/aetherion/config.json`) and retry.
