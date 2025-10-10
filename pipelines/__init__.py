"""
Pipelines package for investment report extraction.
Contains modules for crawling, scraping, filtering, and document extraction.
"""

# Make key modules easily importable
from . import crawler
from . import ir_doc_extract
from . import scraper
from . import ir_extractor_exact
from . import _filters
from . import _guidance

__all__ = [
    'crawler',
    'ir_doc_extract',
    'scraper',
    'ir_extractor_exact',
    '_filters',
    '_guidance',
]

