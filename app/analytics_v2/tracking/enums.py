from __future__ import annotations

from enum import Enum


class TrackState(str, Enum):
    NEW_CANDIDATE = "NEW_CANDIDATE"
    PROBATION = "PROBATION"
    CONFIRMED = "CONFIRMED"
    SHADOW = "SHADOW"
    TERMINATED = "TERMINATED"
