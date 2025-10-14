# Test Mode Implementation

## Overview
Implemented `test=True` functionality across the investment report extraction pipeline to enable quick testing with a limited subset of companies.

## Changes Made

### 1. **crawler.py** (Lines 635-639)
Added logic to limit companies to first 3 when `test=True`:

```python
# Limit to first 3 companies in test mode
if test:
    original_count = len(companies)
    companies = companies[:3]
    logger.info(f"🧪 TEST MODE: Limited to first {len(companies)} companies (out of {original_count})")
```

**Impact**: When `test=True`, only the first 3 DOW 30 companies will be extracted from CNBC.

### 2. **ir_extractor_exact.py** (Lines 487-491)
Added logic to limit IR page extraction to first 3 companies:

```python
# Limit to first 3 companies in test mode
if test:
    original_count = len(companies)
    companies = companies[:3]
    logger.info(f"🧪 TEST MODE: Processing only {len(companies)} companies (out of {original_count})")
```

**Impact**: Even if more companies are passed to the IR extractor, it will only process the first 3 in test mode.

### 3. **ir_extraction_dag.py** (Lines 154-168)
Updated the Airflow DAG to:
- Use `asyncio.run()` to properly execute the async crawler function
- Pass `test=True` and `debug=True` parameters
- Add enhanced logging for test mode

```python
def crawl_dow30(**context):
    """Crawl DOW 30 IR pages with Airflow logging"""
    import asyncio
    task_logger = logging.getLogger('airflow.task')
    task_logger.info("Starting DOW 30 IR pages crawl...")
    task_logger.info("🧪 Running in TEST MODE - processing only first 3 companies for quick testing")
    
    try:
        # Run the async crawler with test=True for quick testing
        result = asyncio.run(crawler.crawl_dow30_ir_pages(test=True, debug=True))
        task_logger.info(f"✅ Crawl completed successfully")
        task_logger.info(f"Companies processed: {len(result.get('companies', []))}")
        task_logger.info(f"IR pages found: {len(result.get('ir_pages', []))}")
        return result
    except Exception as e:
        task_logger.error(f"❌ Crawl failed: {str(e)}", exc_info=True)
        raise
```

## Benefits

### ⚡ Speed
- **Full run**: ~30 companies × ~10-15 seconds each = 5-7 minutes
- **Test run**: 3 companies × ~10-15 seconds each = **30-45 seconds**

### 💰 Cost Savings
- Reduced browser automation time
- Fewer HTTP requests
- Lower resource consumption

### 🧪 Quick Feedback
- Validate DAG logic quickly
- Test Airflow integration
- Verify GCS uploads without waiting for full extraction

## Usage

### In Airflow DAG
The DAG is already configured to use test mode by default:
```python
result = asyncio.run(crawler.crawl_dow30_ir_pages(test=True, debug=True))
```

### Standalone Execution
To run crawler directly in test mode:
```python
import asyncio
from pipelines.crawler import crawl_dow30_ir_pages

# Test mode - only 3 companies
result = asyncio.run(crawl_dow30_ir_pages(test=True, debug=True))

# Full mode - all companies
result = asyncio.run(crawl_dow30_ir_pages(test=False, debug=True))
```

### To Switch to Full Mode
When ready for production, update line 161 in `ir_extraction_dag.py`:
```python
# Change from:
result = asyncio.run(crawler.crawl_dow30_ir_pages(test=True, debug=True))

# To:
result = asyncio.run(crawler.crawl_dow30_ir_pages(test=False, debug=True))
```

## Expected Test Output

When running in test mode, you should see logs like:
```
🧪 TEST MODE: Limited to first 3 companies (out of 30)
[1/3] AAPL - Apple Inc.
  ✓ Found IR URL: https://investor.apple.com
[2/3] MSFT - Microsoft Corporation  
  ✓ Found IR URL: https://www.microsoft.com/en-us/Investor
[3/3] AMZN - Amazon.com Inc.
  ✓ Found IR URL: https://ir.aboutamazon.com
```

## Testing Checklist

- [x] Limit companies in crawler
- [x] Limit companies in IR extractor  
- [x] Update DAG to use test mode
- [x] Add asyncio.run() for async function
- [x] Add enhanced logging
- [ ] Test DAG execution in Airflow
- [ ] Verify GCS upload works
- [ ] Switch to full mode for production

## Notes

- The first 3 companies are typically: **AAPL, MSFT, AMZN** (alphabetically from DOW 30)
- Test mode is ideal for development and CI/CD pipelines
- Remember to switch to `test=False` for production runs

