"""Feedback Loop — learn from approved and rejected PRs.

Pipeline:
  PR approved  → Collector → ExampleStore (positive) + AutoIngest (KB chunks)
  PR rejected  → Collector → ExampleStore (negative)
  Nightly beat → Trainer   → BootstrapFewShot → weights/{role}_{tier}.json
               → Workers hot-load updated weights on next task
"""

from __future__ import annotations

from .collector import Collector
from .example_store import ExampleStore, Example
from .trainer import Trainer
from .auto_ingest import AutoIngestor

__all__ = ["Collector", "ExampleStore", "Example", "Trainer", "AutoIngestor"]
