class AnalyzerError(Exception):
    """Base exception for expected application failures."""


class UpstreamError(AnalyzerError):
    pass


class UpstreamResponseError(UpstreamError):
    pass


class QuotaExhaustedError(UpstreamError):
    pass


class ContributorNotFoundError(AnalyzerError):
    pass


class NarrativeUnavailableError(AnalyzerError):
    pass


class InvalidEvidenceError(NarrativeUnavailableError):
    pass
