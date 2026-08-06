"""Nova base analitica stateful para CFTV humano.

Este pacote concentra tracker com maquina de estados, geometria de cena,
regras de intrusao, score composto, filtros e saida operacional.
"""

from .config.loader import load_analytics_config
from .config.schema import AnalyticsConfig
from .events.models import AlarmEvent, EventEvidence
from .pipeline.event_pipeline import AnalyticsEventPipeline
from .scene.geometry import Footpoint, bbox_footpoint
from .tracking.enums import TrackState
from .tracking.tracker import StatefulTracker

__all__ = [
    "AnalyticsConfig",
    "AnalyticsEventPipeline",
    "AlarmEvent",
    "EventEvidence",
    "Footpoint",
    "TrackState",
    "StatefulTracker",
    "bbox_footpoint",
    "load_analytics_config",
]
