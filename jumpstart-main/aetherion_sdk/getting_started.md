# Creating a New Project

To simplify onboarding, Aetherion provides a single installation script that performs the complete setup automatically.

**This script will:**
- Install the required Python version
- Create and activate a virtual environment
- Install `uv` for dependency management
- Install the Aetherion SDK
- Start a local MinIO container to simulate file upload and object storage

```bash
# 1) Create a directory for setup
mkdir DIR_NAME
cd DIR_NAME

# 2) One-Command Installation
/bin/bash -c "$(curl -fsSL https://sdk.sbox.aetherion.io/install.sh)"

# 3) (Optional) Verify the CLI is available
aetherion --help

# 4) Scaffold a new project
aetherion init hello_world 

            #or
#Instead of `hello_world` you can also use your custom agent name to create a scaffold, use the below command to do so:
aetherion init {{your_agent_name}}            

# 5) Go to the project directory
cd hello_world

# 6) Install project dependencies
uv sync

# 7) Activate the virtual environment
source .venv/bin/activate
```

> **Note:** First-time users may need to add a few team-specific configurations, which will be provided before the hackathon.

---

[Next Step: Quick Start](running_with_cli.md)
