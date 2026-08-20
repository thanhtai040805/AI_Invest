"""
Financial OCR Pipeline for Vietnamese Financial Statements (BCTC)
"""

import sys
import os

# Fix Windows aiodns DNS resolution bug by forcing ThreadedResolver before aiohttp import
sys.modules["aiodns"] = None
os.environ["AIOHTTP_NO_EXTENSIONS"] = "1"

try:
    import aiohttp
    import aiohttp.resolver
    aiohttp.resolver.DefaultResolver = aiohttp.ThreadedResolver
    aiohttp.resolver.AsyncResolver = aiohttp.ThreadedResolver
except Exception:
    pass

from .config import FinancialProfileConfig, load_profile
from .page_classifier import PageClassifier, PageClassificationResult
from .region_classifier import RegionClassifier
from .benchmarking import BenchmarkTracker
from .pipeline import FinancialOcrPipeline

__all__ = [
    "FinancialProfileConfig",
    "load_profile",
    "PageClassifier",
    "PageClassificationResult",
    "RegionClassifier",
    "BenchmarkTracker",
    "FinancialOcrPipeline",
]
