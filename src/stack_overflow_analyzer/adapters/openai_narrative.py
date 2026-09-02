import json
from typing import Any

import structlog
from openai import APIError, AsyncOpenAI, RateLimitError

from stack_overflow_analyzer.domain.exceptions import (
    NarrativeRateLimitError,
    NarrativeUnavailableError,
)
from stack_overflow_analyzer.domain.models import ContributorAnalysis, NarrativeOutput
from stack_overflow_analyzer.ports.narrative import NarrativeGenerator

logger = structlog.get_logger(__name__)

INSTRUCTIONS = """Role: Write a natural, evidence-grounded assessment of one Stack Overflow
contributor's activity in one technology tag during one selected month.

Goal: Describe what the contributor's actual participation says about their engagement with the tag
that month. Explain whether the contribution was notable within a benchmark made up of the tag's
official all-time Top-20 answerers, then describe how the month differed from the previous
equivalent month and what measured pattern best explains the result.

Writing requirements:
- Write fluid explanatory prose for a human reader, not a classification or scorecard.
- Open with what the contributor did and why it is meaningful. Never begin with a categorical label
  such as "Very active", "Active", "Minimally active", or "Not active", and never format the opening
  as a label followed by a dash or colon.
- Synthesize the overall story. Do not produce a metric-by-metric recital or repeat the dashboard.
- Use only the few strongest numbers when they make the interpretation more concrete.
- Avoid repeating the same fact across fields. Each field should add a distinct insight.
- When has_qualifying_answers is false, state plainly that no qualifying answers were found in the
  selected tag and month. Do not assign an activity label, infer broader inactivity, or manufacture
  a benchmark position. Focus any useful interpretation on comparison with the previous month.
- notable_contribution should connect the month's participation, its benchmark significance, and
  the broad direction of change from the previous month in two or three natural sentences.
- ranking_explanation should explain the main measured driver of the benchmark position.
- peer_comparison should interpret what the comparison means, including a material caveat when one
  exists, rather than listing every peer mean.
- period_change should describe the trajectory and its significance rather than listing every delta.

Evidence requirements:
- The supplied JSON is the entire source of truth. Do not calculate new metrics, invent thresholds,
  introduce facts, or cite evidence outside the supplied evidence objects.
- evidence_ids is the machine-readable evidence channel. Populate it with exact IDs from the
  evidence array, but never print evidence IDs, JSON keys, variable names, field paths, or
  parenthetical citations in any prose field. Refer to evidence in ordinary language instead.
- The rank is a benchmark-cohort rank, never a global period rank. The cohort consists of
  historically strong answerers, while its comparison values describe only the selected month.
- Scope every conclusion to the requested tag and period, not the contributor's overall activity.
- Scope topic_fingerprint to co-occurring tags on selected-tag answers; do not treat it as the
  contributor's overall interests.
- Calibrate the structured confidence field to evidence breadth and strength.
"""


class OpenAINarrativeGenerator(NarrativeGenerator):
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
        client: Any | None = None,
    ) -> None:
        self._client = client or (
            AsyncOpenAI(api_key=api_key, timeout=timeout_seconds) if api_key else None
        )
        self._owns_client = client is None
        self._model = model

    async def generate(self, analysis: ContributorAnalysis) -> NarrativeOutput:
        if self._client is None:
            raise NarrativeUnavailableError(
                "SOA_OPENAI_API_KEY is required for narrative generation"
            )
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=INSTRUCTIONS,
                input=json.dumps(analysis.model_dump(mode="json"), separators=(",", ":")),
                text_format=NarrativeOutput,
                store=False,
            )
        except RateLimitError as exc:
            error_code = getattr(exc, "code", None)
            logger.error(
                "narrative_provider_failed",
                operation="generate_narrative",
                provider="openai",
                model=self._model,
                error_type=type(exc).__name__,
                provider_error_code=error_code,
                provider_request_id=getattr(exc, "request_id", None),
            )
            if error_code == "insufficient_quota":
                detail = "OpenAI API quota exhausted; check billing and usage limits"
            else:
                detail = "OpenAI API rate limit exceeded; retry later"
            raise NarrativeRateLimitError(detail) from exc
        except APIError as exc:
            logger.error(
                "narrative_provider_failed",
                operation="generate_narrative",
                provider="openai",
                model=self._model,
                error_type=type(exc).__name__,
                provider_error_code=getattr(exc, "code", None),
                provider_request_id=getattr(exc, "request_id", None),
            )
            raise NarrativeUnavailableError("OpenAI narrative request failed") from exc
        except (ValueError, TypeError) as exc:
            raise NarrativeUnavailableError("OpenAI returned invalid structured output") from exc

        parsed = response.output_parsed
        if parsed is None or not isinstance(parsed, NarrativeOutput):
            raise NarrativeUnavailableError("OpenAI returned no structured narrative")
        return parsed

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.close()
