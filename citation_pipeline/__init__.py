__all__ = ["VerificationPipeline", "FullCitationPipeline"]


def __getattr__(name: str):
    if name == "FullCitationPipeline":
        from .full_pipeline import FullCitationPipeline

        return FullCitationPipeline
    if name == "VerificationPipeline":
        from .verification.pipeline import VerificationPipeline

        return VerificationPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
