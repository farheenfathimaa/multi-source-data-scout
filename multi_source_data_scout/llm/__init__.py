"""Optional LLM-assisted run summary (feature-flagged, off by default)."""

from multi_source_data_scout.llm.summary import summarize_run

__all__ = ["summarize_run"]