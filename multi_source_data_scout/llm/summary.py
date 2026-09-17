"""One-paragraph, natural-language summary of a pipeline run via Groq's
free-tier API. Entirely optional: without a GROQ_API_KEY this step logs a
skip message and the rest of the pipeline runs identically.
"""

from __future__ import annotations

from multi_source_data_scout.config import GROQ_MODEL, GROQ_TIMEOUT_SECONDS
from multi_source_data_scout.logutil import get_logger
from multi_source_data_scout.models import PipelineReport

log = get_logger("llm.summary")

_SKIP_MESSAGE = "LLM summary skipped (no API key configured)"


def summarize_run(report: PipelineReport, api_key: str | None) -> str | None:
    """Generate (or skip) the natural-language summary.

    Returns the summary text, or None when skipped / failed.
    """
    if not api_key:
        log.info(_SKIP_MESSAGE)
        return None

    try:
        from groq import Groq  # imported lazily so the step is truly optional

        client = Groq(api_key=api_key, timeout=GROQ_TIMEOUT_SECONDS)
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior data engineer summarizing a pipeline "
                        "run for a data team. Reply with ONE paragraph (5-8 "
                        "sentences), factual and concise."
                    ),
                },
                {"role": "user", "content": _build_prompt(report)},
            ],
            max_tokens=300,
            temperature=0.3,
        )
        summary = (response.choices[0].message.content or "").strip()
        log.info("LLM run summary generated (%d chars)", len(summary))
        return summary
    except Exception as exc:  # never let the optional step fail the pipeline
        log.warning("LLM summary failed, continuing without it: %s", exc)
        return None


def _build_prompt(report: PipelineReport) -> str:
    stage_lines = []
    for stage in report.stages:
        stage_lines.append(
            f"- {stage.stage}: collected={stage.rows_collected} "
            f"valid={stage.rows_valid} loaded={stage.rows_loaded} "
            f"quarantined={stage.rows_quarantined} errors={stage.errors} "
            f"failed_ratio={stage.failed_ratio:.1%} "
            f"duration={stage.duration_seconds:.1f}s"
        )

    anomalies = []
    for stage in report.stages:
        if stage.rows_quarantined:
            anomalies.append(f"{stage.stage} quarantined {stage.rows_quarantined} rows")
        if stage.errors:
            anomalies.append(f"{stage.stage} hit {stage.errors} errors")
    if report.breaches_threshold:
        anomalies.append("the error-rate gate was breached")
    if not anomalies:
        anomalies.append("no notable anomalies")

    return (
        "Summarize this run of the multi-source-data-scout pipeline.\n"
        "Context: it scrapes quotes.toscrape.com/js and books.toscrape.com with "
        "Playwright, cross-references books against the Open Library API, and "
        "lands data in SQLite.\n\n"
        "Stages:\n"
        + "\n".join(stage_lines)
        + "\n\nAnomalies: "
        + "; ".join(anomalies)
        + (
            f"\n\nThreshold gate: {'BREACHED' if report.breaches_threshold else 'OK'} "
            f"(threshold {report.threshold:.0%})"
        )
    )