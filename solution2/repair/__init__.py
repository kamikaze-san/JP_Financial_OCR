"""Automated, location-only retry loop driven by the v2 verifier (see loop.py)."""
from .loop import Attempt, RepairConfig, RepairResult, repair
from .page import PdfPage
from .reader import OllamaReader, TextLayerReader, build_prompt

__all__ = ["Attempt", "RepairConfig", "RepairResult", "repair", "PdfPage", "OllamaReader",
           "TextLayerReader", "build_prompt"]
