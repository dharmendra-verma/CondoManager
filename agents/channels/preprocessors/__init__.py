"""Voice → text and image → text preprocessors for channel adapters.

Jira: CM-29  | Epic: CM-Epic 3 (Channel Adapters)  | Phase 0

Two Protocols + two no-op stubs. CM-34 (Azure AI Speech) and CM-35
(Azure AI Vision) replace the stubs with real implementations via
dependency injection at app startup — channel adapters take an
``AudioTranscriber`` / ``ImageOcr`` parameter (typed as the Protocol) and
the orchestrator boot wires in either the real implementation or the
stub based on env config.
"""

from __future__ import annotations

from .audio import AudioTranscriber, NoopAudioTranscriber
from .image import ImageOcr, NoopImageOcr

__all__ = [
    "AudioTranscriber",
    "ImageOcr",
    "NoopAudioTranscriber",
    "NoopImageOcr",
]
