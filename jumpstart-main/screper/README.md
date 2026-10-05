# Scraper

A declarative web scraping SDK. Describe your scrape as a list of actions (`navigate`, `click_button`, `fill_value`, `extract_text`, …) in YAML / JSON / a Python dict, and run it against a [Playwright](https://playwright.dev/python/) or [Steel](https://steel.dev/) browser.

Includes built-in handling for Oracle ERP UIs (Glass Pane waits, ADF and Redwood dropdown semantics, fallback selector chains).

## Table of Contents

- [Install & Import](#install--import)
- [Quick Example](#quick-example)
- [Configuration](#configuration)
  - [The `data` section](#the-data-section)
  - [The `scraper` section](#the-scraper-section)
  - [The `actions` section](#the-actions-section)
- [Python API](#python-api)
- [Action Reference](#action-reference)
- [Oracle ERP Notes](#oracle-erp-notes)
- [Full YAML Example](#full-yaml-example)

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

## Quick Example

```python
from scraper.scraper_factory import ScraperFactory
from scraper.scraper_config import ScraperConfig

config = ScraperConfig.from_dict({
    "scraper": {"type": "playwright", "headless": "true"},
    "actions": [
        {"type": "navigate", "args": {"url": "https://example.com"}},
        {"type": "extract_text", "args": {"selector": "h1", "variable_name": "title"}},
    ],
})

scraper = ScraperFactory.get_scraper("playwright", config)
scraper.run()
print(config.title)  # extracted variables become attributes on the config
```

## Configuration

A config has three top-level sections: `data`, `scraper`, and `actions`. You can load it from a dict, JSON string, JSON file, or YAML file.

```python
config = ScraperConfig.load("scraper_config.yaml")     # auto-detects JSON vs YAML
config = ScraperConfig.from_dict({...})
config = ScraperConfig.from_json('{"scraper": {...}, "actions": [...]}')
```

### The `data` section

User-supplied variables you can reference from actions. Keys in `data` are also visible to action arguments via the `${VAR_NAME}` syntax, and `${ENV_VAR}` patterns are interpolated from process environment variables at load time.

```yaml
data:
  url: "https://example.com"
  api_key: ${OPENAI_API_KEY}   # resolved from os.getenv("OPENAI_API_KEY")
```

### The `scraper` section

Browser/engine settings.

| Field | Description |
|-------|-------------|
| `type` | `"playwright"` (default) or `"steel"`. |
| `headless` | `"true"` / `"false"`. Playwright only. |
| `STEEL_API_KEY` | Steel API key. Required when `type: steel`. Supports `${STEEL_API_KEY}` interpolation. |
| `proxy.server` | Proxy `host:port`. |
| `proxy.username`, `proxy.password` | Proxy credentials. |
| `default_delay.page_default_timeout` | Default action timeout in milliseconds. |
| `default_delay.page_default_navigation_timeout` | Default navigation timeout in milliseconds. |

```yaml
scraper:
  type: playwright
  headless: "true"
  default_delay:
    page_default_timeout: 20000
    page_default_navigation_timeout: 25000
```

### The `actions` section

A list of action objects. Each has a `type`, an `args` dict, and optional metadata.

| Key | Purpose |
|-----|---------|
| `type` | Action name (see the [Action Reference](#action-reference)). |
| `args` | Arguments passed to the action. Strings support `${VAR_NAME}` interpolation. |
| `_playwright` | Optional Playwright locator (Python expression eval'd with `page` in scope) — used as a fallback when the primary selector fails. |
| `_playwright_alt` | Last-resort Playwright locator. |
| `_name` | Human-readable label for logging. |
| `_select` | When true on `fill_value`, force `<select>` semantics. |

## Python API

### `ScraperFactory`

| Method | Description |
|--------|-------------|
| `ScraperFactory.get_scraper(type, config)` | Return a `Scraper` instance. `type` is `"playwright"` or `"steel"`. |

### `ScraperConfig`

| Method | Description |
|--------|-------------|
| `ScraperConfig.load(file_path)` | Load from a JSON or YAML file. |
| `ScraperConfig.from_dict(d)` | Load from a Python dict. |
| `ScraperConfig.from_json(s)` | Load from a JSON string. |
| `.to_dict()` / `.to_json()` | Serialise back out. |
| `.addAttribute(key, value)` | Add a value to the config and its `data_map`. |
| `.argValues(action_args)` | Resolve `${VAR}` patterns in action args against the `data_map`. |

### `Scraper`

| Method | Description |
|--------|-------------|
| `.setup()` | Initialise browser and page. Called automatically by `run()`. |
| `.run(*args, **kwargs)` | Execute the configured actions; returns a list of processed items (from loop actions). |
| `.cleanup()` | Close the browser. Called automatically at the end of `run()`. |
| `.eval_condition(condition, data_map)` | Evaluate a Python expression with `data_map` and the helper `element_exists(selector)` available. |

Backends:

- **PlaywrightScraper** — local Chromium via Playwright. Pick this for general-purpose scraping and when you have proxy configuration of your own.
- **SteelScraper** — connects Playwright to a remote [Steel](https://steel.dev/) browser session for residential proxies and CAPTCHA handling. Requires `STEEL_API_KEY`.

## Action Reference

### `navigate`

Navigate to a URL. Includes Oracle Glass Pane handling — waits for known loading overlays to disappear after page load.

| Arg | Description |
|-----|-------------|
| `url` | The URL to navigate to. |
| `timeout` | Navigation timeout in milliseconds (optional). |

```yaml
- type: navigate
  args:
    url: "${url}"
```

### `click_button`

Click an element. Supports a fallback chain: tries each CSS selector in `selector`, then `_playwright`, then `_playwright_alt`.

| Arg | Description |
|-----|-------------|
| `selector` | CSS selector (string), comma-separated CSS list, or list of selectors. XPath expressions starting with `/` or `(` are treated as single selectors. |
| `timeout` | Wait timeout in milliseconds. |

```yaml
- type: click_button
  args:
    selector: "button.primary, button[type='submit']"
  _playwright: "page.get_by_role('button', name='Submit')"
  _playwright_alt: "page.locator('button:has-text(\"Submit\")')"
```

### `fill_value`

Fill an input field. Handles regular inputs, textareas, native `<select>` elements, and Oracle's custom dropdown widgets.

| Arg | Description |
|-----|-------------|
| `selector` | CSS selector (or list) for the input. |
| `value` | Value to type or select. Supports `${VAR}` interpolation. |
| `press_after` | Optional key to press after filling (e.g. `"Enter"`, `"Tab"`). |
| `press_after_delay` | Delay in ms before pressing the key. |
| `is_oracle_dropdown` | When `true`, use Oracle dropdown semantics. |
| `_select` | When `true`, force `<select>` semantics. |

```yaml
- type: fill_value
  args:
    selector: "input[aria-label='Where from?']"
    value: "${from}"
    press_after: "Enter"
```

### `extract_text`

Extract `innerText` of an element and store it in the config.

| Arg | Description |
|-----|-------------|
| `selector` | CSS selector for the element. |
| `variable_name` | Name to store the text under. Access as `config.<variable_name>` or via `${variable_name}` in later actions. |

### `extract_html`

Extract the `outerHTML` of an element, or the full page HTML if `selector` is empty.

| Arg | Description |
|-----|-------------|
| `selector` | CSS selector. Empty string ⇒ full page. |
| `variable_name` | Name to store under. |

### `screenshot`

Take a full-page screenshot. Always writes to local disk; optionally uploads to S3.

| Arg | Description |
|-----|-------------|
| `prefix` | Filename prefix. The final name is `<prefix>_<timestamp>.png`. |
| `path` | Local directory. |
| `bucket_name` | S3 bucket. If provided with `agent_run_id` and `activity_name`, uploads to `{agent_run_id}/{activity_name}/{filename}` and deletes the local file. |
| `agent_run_id` | Optional, used in the S3 path. |
| `activity_name` | Optional, used in the S3 path. |
| `metadata` | Optional metadata dict to attach in S3. |
| `variable_name` | Result key on the config (default `screenshot_result`). |

### `save_html`

Save the current page HTML to a local file.

| Arg | Description |
|-----|-------------|
| `prefix` | Filename prefix. |
| `path` | Directory. Created if missing. |

### `create_pdf_from_page`

Generate a PDF of the rendered page and upload it (plus the page HTML) to S3.

| Arg | Description |
|-----|-------------|
| `path` | Local directory for the temporary file. |
| `pdf_name` | File stem (no extension). |
| `bucket_name` | S3 bucket. |
| `metadata` | Metadata dict for the upload. |
| `variable_name` | Result key on the config (default `pdf_upload_result`). |

### `random_wait`

Sleep for a random duration.

| Arg | Description |
|-----|-------------|
| `min_seconds` | Minimum wait (default `1.0`). |
| `max_seconds` | Maximum wait (default `5.0`). |

### `random_scroll`

Scroll down 300 pixels, three times. Useful to simulate human-like behaviour or trigger lazy-loaded content.

### `random_wait_scroll`

`random_wait` followed by `random_scroll`.

| Arg | Description |
|-----|-------------|
| `min_seconds` | Minimum wait. |
| `max_seconds` | Maximum wait. |

### Flow control: `loop` and `if`

`loop` iterates over a list of values and re-runs a sub-sequence of actions for each. `if` runs a sub-sequence only when a condition holds.

```yaml
- type: loop
  args:
    variable_name: category_url
    values:
      - "/category/books"
      - "/category/electronics"
  steps:
    - type: navigate
      args: { url: "https://example.com${category_url}" }
    - type: extract_text
      args: { selector: "h1", variable_name: "category_title" }

- type: if
  condition: "element_exists('.popup-close-button')"
  steps:
    - type: click_button
      args: { selector: ".popup-close-button" }
```

`condition` is a Python expression with access to the config's `data_map` and the helper `element_exists(selector)`.

## Oracle ERP Notes

**Glass Pane** — after `navigate`, the SDK looks for and waits out the following loading overlays before returning:

- ADF (Classic): `.AFBlockingGlassPane`, `[id*='glassPane']`, `.p_AFBusy`
- Redwood (Modern): `.oj-dialog-layer`, `.oj-mask`, `[class*='oj-progress']`, `[class*='fnd-progress']`, `.fnd-loading-indicator`
- Generic: `[aria-label*='Loading']`, `[aria-busy='true']`

**Fallback selector chain** — `click_button` and `fill_value` try selectors in this order:

1. `selector` (each entry if it's a list / comma-separated string)
2. `_playwright` (Python expression eval'd with `page` in scope)
3. `_playwright_alt` (last-resort Playwright locator)

This makes configs portable across Oracle UI versions (ADF vs Redwood) without rewriting them.

**Oracle dropdowns** — set `is_oracle_dropdown: true` on `fill_value` to use the platform's dropdown semantics (type-ahead, wait for popup list, exact-text match, click match or press Enter, wait for popup to close).

## Full YAML Example

```yaml
data:
  url: "https://www.google.com"
  query: "Aetherion Agents"

scraper:
  type: playwright
  headless: "false"
  default_delay:
    page_default_timeout: 20000

actions:
  - type: navigate
    args:
      url: "${url}"

  - type: fill_value
    args:
      selector: "textarea[name='q']"
      value: "${query}"
      press_after: "Enter"

  - type: extract_text
    args:
      selector: "#search"
      variable_name: "search_results"

  - type: screenshot
    args:
      prefix: "results"
      path: "./screenshots"
```

```python
from scraper.scraper_factory import ScraperFactory
from scraper.scraper_config import ScraperConfig

config = ScraperConfig.load("scraper_config.yaml")
scraper = ScraperFactory.get_scraper("playwright", config)
scraper.run()
print(config.search_results)
```
