# Antennae - Webhooks, Consumers, and Connectors

Antennae is a collection of webhooks, consumers, and connectors for Aetherion. It is used to handle webhook events from various sources and forward them to the appropriate consumers and connectors.

## 📂 Directory Structure

```
antennae/
├── README.md (this file)
├── consumers/               # 📥 Event consumers
│   └── sqs-slack-consumer/  # 💬 SQS consumer for Slack events
└── webhooks/
    └── aws/
        ├── generic-webhook/ # 🔗 Generic webhook handler
        ├── slack-webhook/   # 💬 Slack events handler
        └── gmail-webhook/   # 📧 Gmail webhook handler
```

### Run tests
From the `antennae` directory:
```bash
cd antennae
pytest
```
All tests live under `antennae/tests/` (135 tests for webhooks and consumers).

**With coverage** (requires `pytest-cov`):
```bash
pytest --cov=webhooks --cov=consumers --cov-report=term-missing
```
This reports line coverage per file and overall (e.g. ~74% total); use `--cov-report=html` for an HTML report.

### For Daily Use
- **Deploy changes**: Just push to a `branch` and raise a PR against `main`. Once the PR is merged, the changes will be deployed to the appropriate environment.
