# Company Data Scraping API

A Python-based web scraping and data extraction tool for Russian business registries, bankruptcy data, and company analysis. Provides a FastAPI server to query company information by INN (Tax ID).

## Features

- **Company Data**: Registration details, founders, main activities from list-org.com
- **Financials**: Balance sheets, ratios, and filtering from list-org.com
- **Bankruptcy Lots**: Parsing and enrichment from tbankrot.ru with AI-powered debtor analysis
- **RDL Checks**: CEO and founder disqualification status from nalog.ru
- **Extended Info**: CEOs, beneficiaries, employees, court debts from zachestnyibiznes.ru
- **Batch ZChB Processing**: Excel-based extraction of CEOs, founders, and beneficiaries with JSON/Excel output
- **Resumable Batch Runs**: Deduplication by INN, cached results, modal-content stabilization, and progress saving after each company
- **Human-like Navigation**: Randomized scrolling and configurable pauses between ZChB actions
- **Bankruptcy Status**: Individual checks via API
- **PDF Extraction**: Text extraction from PDF URLs
- **Debt Verification Pipeline**: Multi-stage analysis (structural risks, financial health, legal verification) for bankruptcy debt assessment via debt_verifier module
- **Proxy Rotation**: Support for rotating proxies via proxies.txt file for scraping reliability
- **Safe Browser Lifecycle**: Persistent browser context with guarded page/context shutdown and proxy switching

## Quick Start

### 1. Prerequisites

- Python 3.10+ (recommended: use [pyenv](https://github.com/pyenv/pyenv) with `.python-version` file)
- [uv](https://docs.astral.sh/uv/) (fast Python package manager)
- Git

### 2. Clone and Setup Environment

```bash
git clone <repository-url>
cd playwright_ZChB
```

### 3. Configure Environment Variables

Copy the example environment file and fill in the values (we will provide you with the actual credentials):

```bash
cp .env.example .env
# Edit .env with your values:
# APIKEY_2CAPTCHA=your_2captcha_key
# api_cloud=your_api_cloud_key
# ZCHB_LOGIN=your_zchb_username
# ZCHB_PWD=your_zchb_password
# OPENROUTER_APIKEY=sk-or-v1-your_openrouter_key
# TBANKROT_AUTH_TOKEN=your_tbankrot_token
# TBANKROT_UID=your_tbankrot_uid
# TBANKROT_HASH=your_tbankrot_hash
# TBANKROT_DEVICE_ID=your_tbankrot_device_id
# TBANKROT_S360HASH=your_tbankrot_s360hash
```

**Required services:**
- 2Captcha for captcha solving
- API Cloud for bankruptcy checks
- OpenRouter for AI enrichment
- Zachestnyibiznes.ru credentials
- TBankrot.ru authentication tokens

### 4. Configure Proxies (Optional but Recommended)

Create a `proxies.txt` file in the project root with one proxy per line in format `username:password@host:port` or `host:port`:

```
user1:pass1@proxy1.example.com:8080
user2:pass2@proxy2.example.com:3128
```

The [ProxyManager](src/proxy_manager.py) will automatically load and rotate through these proxies for scraping operations to improve reliability and avoid rate limiting.

### 5. Install Dependencies

Install uv if you haven't already:

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then install project dependencies:

```bash
uv sync  # Installs from pyproject.toml and creates .venv
```

### 6. Install Playwright Browsers

```bash
uv run playwright install chromium
```

### 7. Run the API Server

#### Linux/macOS (with tmux for background running):
```bash
tmux new -s zchb
uv run -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

#### Windows (avoid --reload due to Playwright compatibility):
```bash
uv run -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

The API will be available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

### 8. Test the API

```bash
# Test company card data
curl "http://localhost:8000/company_card/6312126585"

# Test financial data
curl "http://localhost:8000/company_finances/6312126585?years=2020,2021&codes=1200,1240"

# Test RDL check
curl "http://localhost:8000/company_rdl/6312126585?publish_date=26.03.2025"

# Test extended ZChB data
curl "http://localhost:8000/company_extended/6312126585"
```

## API Endpoints

| Endpoint | Description | Parameters |
|----------|-------------|------------|
| `GET /company_card/{inn}` | Company registration data | `inn` (required) |
| `GET /company_finances/{inn}` | Financial statements & ratios | `inn`, `years`, `codes` |
| `GET /company_rdl/{inn}` | CEO/founder disqualification check | `inn`, `publish_date` (dd.mm.yyyy) |
| `GET /company_extended/{inn}` | ZChB: CEOs, beneficiaries, employees | `inn` |
| `GET /bankrot/{inn}` | Individual bankruptcy status | `inn` |
| `GET /pdf_extract` | Extract text from PDF URL | `url` |
| `GET /docs` | Interactive API documentation | - |

## Project Structure

```
.
├── main.py                 # FastAPI server
├── .env.example           # Environment variables template
├── proxies.txt            # Proxy list for rotation (one per line)
├── src/
│   ├── browser.py         # Persistent browser context, safe shutdown, and proxy retry logic
│   ├── proxy_manager.py   # Proxy rotation and management
│   ├── listorg/           # Company data scraping
│   ├── tbankrot/          # Bankruptcy lot parsing & AI enrichment
│   ├── ZChB/              # Extended company data extraction
│   │   ├── flows.py                   # ZChB modal navigation, extraction, and human-like pauses
│   │   ├── flows2.py                  # Alternative/experimental extraction flow
│   │   ├── main.py                    # Single-INN development run
│   │   ├── main_enhanced.py           # Basic Excel batch processor
│   │   ├── main_enhanced_waits.py     # Resumable batch processor with stable waits and safe saves
│   │   └── not_banned.py              # Excel filter for debtor/creditor rejection columns
│   └── debt_verifier/     # Multi-stage debt verification pipeline
│       ├── all_stages.py              # Stage 0: Basic data validation
│       ├── stage_3_case_analysis.py   # Stage 3: Legal verification scoring
│       ├── stage_structural__finances_analysis.py  # Stage 2: Financial analysis
│       └── stage_structural_analysis_bankruptcy_risks.py  # Stage 1: Bankruptcy risk assessment
│   └── utils.py           # Shared utilities
├── data/                  # Outputs, logs, screenshots
├── cache/                 # Scraping cache
└── CONTRIBUTING.md        # Development guidelines
```

## Running Scrapers Standalone

For development/testing:

```bash
# ListOrg company data
uv run src/listorg/main.py

# TBankrot lot parsing
uv run src/tbankrot/main.py

# ZChB extended extraction
uv run python -m src.ZChB.main
```

## Batch ZChB Processing

The project includes two standalone Excel batch processors. Their input path,
INN column, output name, browser mode, and browser data directory are configured
as constants near the top of each script.

### Basic batch processor

`src/ZChB/main_enhanced.py`:

- logs in once and reuses the persistent browser session;
- reads INNs from an Excel column;
- extracts CEO history, founder history, and beneficiaries;
- writes the extracted data into a new Excel column;
- saves the complete result set as JSON;
- captures a screenshot when processing an INN fails.

```bash
uv run python -m src.ZChB.main_enhanced
```

### Resumable batch processor

`src/ZChB/main_enhanced_waits.py` is intended for longer runs. In addition to
the basic workflow, it:

- normalizes and deduplicates INNs while preserving their original order;
- waits for asynchronously loaded modal content to stabilize before extraction;
- loads previously saved JSON results and skips already processed INNs;
- saves progress every `SAVE_EVERY_N` unique INNs (currently `1`);
- writes JSON and Excel through temporary files before replacing the outputs;
- performs a final save when the run finishes or is interrupted;
- safely closes pages and the persistent browser context.

```bash
uv run python -m src.ZChB.main_enhanced_waits
```

Batch outputs are written to `data/output/`, logs to `data/logs/`, and failure
screenshots to `data/screenshots/`.

## Filtering Rejected Excel Rows

`src/ZChB/not_banned.py` removes rows where either `Брак по должнику` or
`Брак по кредитору` is filled. It saves a new workbook with `_not_banned`
appended to the original filename.

Set `INPUT_FILE` at the top of the script, then run:

```bash
uv run python -m src.ZChB.not_banned
```

## Recent ZChB Changes

- Added configurable randomized pauses, pre-click delays, post-open delays, and varied scrolling in `flows.py`.
- Updated `main.py` to use the shared persistent `Browser` context and to close it reliably.
- Hardened page, context, Playwright, and proxy-switch cleanup in `browser.py` so cleanup failures are logged without hiding the main operation.
- Added basic and resumable Excel batch-processing workflows for CEO, founder, and beneficiary extraction.
- Added an Excel cleanup utility for debtor/creditor rejection markers.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for code style, testing, and contribution guidelines.

## Troubleshooting

- **Captcha issues**: Ensure `APIKEY_2CAPTCHA` is valid and has credits
- **Authentication errors**: Check TBankrot and ZChB credentials in `.env`
- **Browser crashes**: Delete `datadir/` and restart (resets browser state)
- **Proxy failures**: Verify `proxies.txt` format and proxy server availability. Check logs for proxy errors.
- **uv not found**: Install from https://docs.astral.sh/uv/
- **Playwright errors**: Run `uv run playwright install chromium`
- **Port conflicts**: Change `--port 8000` to another port

## License

[Add license information here]

---

**Note**: This project is for educational purposes only. It handles sensitive business data. Ensure compliance with Russian data protection laws and terms of service for scraped websites.
