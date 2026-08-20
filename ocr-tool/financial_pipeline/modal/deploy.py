"""Deployment entrypoint for the financial-ocr app.

Imports the app plus every Modal class that must be registered on deploy so
cross-app lookups (``modal.Cls.from_name("financial-ocr", ...)``) resolve.
Kept separate from :mod:`.app` to avoid the classify -> app -> supervisor ->
classify import cycle that Modal's direct-module import triggers.
"""

from . import classify as _classify  # noqa: F401
from . import supervisor as _supervisor  # noqa: F401
from .app import app  # noqa: F401
