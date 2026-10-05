# Scraper

The `scraper` package is a declarative web scraping SDK. You describe what to do as a list of actions (in YAML, JSON, or a Python dict), and the SDK runs them against a Playwright or Steel browser session.

It includes built-in support for Oracle ERP UIs (Glass Pane wait handling, dropdown semantics, fallback selectors).

## Install & Import

```bash
pip install scraper
# or
uv add scraper
```

```python
from scraper.scraper_factory import ScraperFactory
from scraper.scraper_config import ScraperConfig
```

## Where to Next

See [`README.md`](README.md) for the full reference: config structure, action types, code examples, and Oracle ERP notes.
