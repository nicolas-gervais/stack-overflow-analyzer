import json
from typing import Any

import structlog
from openai import APIError, AsyncOpenAI

from stack_overflow_analyzer.domain.exceptions import NarrativeUnavailableError
from stack_overflow_analyzer.domain.models import ContributorAnalysis, NarrativeOutput
from stack_overflow_analyzer.ports.narrative import NarrativeGenerator

logger = structlog.get_logger(__name__)

INSTRUCTIONS = """You explain deterministic Stack Overflow contributor analytics.
The supplied JSON is the entire source of truth. Do not calculate new metrics, introduce facts,
or cite evidence outside the supplied evidence objects. Use only exact evidence IDs from the
evidence array. Explain rank, peer comparison, prior-period change, and topic fingerprint
concisely. A root-cause statement must be framed as a hypothesis and must be null when the
evidence does not justify one. Calibrate confidence to evidence breadth and strength.
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
        except APIError as exc:
            logger.error(
                "narrative_provider_failed",
                operation="generate_narrative",
                provider="openai",
                model=self._model,
                error_type=type(exc).__name__,
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
