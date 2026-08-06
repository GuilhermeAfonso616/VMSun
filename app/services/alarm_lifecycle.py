"""Define como eventos viram alarmes operacionais.

Aqui ficam as regras de abertura, fechamento, correlação temporal e categoria
para evitar duplicação de alarmes quando o mesmo track gera vários eventos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha1

from sqlalchemy import or_

from app.core.config import settings
from app.core.timezone import utc_now_naive
from app.db.models import Event, IncidentTimeline


@dataclass(slots=True)
class AlarmLifecycleDecision:
    alarm_eligible: bool
    lifecycle_action: str
    alarm_category: str | None
    correlation_key: str | None
    reason: str
    status: str
    is_alarm_active: bool
    related_event_id: int | None = None
    resolved_by_event_id: int | None = None
    resolved_at: datetime | None = None


class AlarmLifecycleService:
    OPEN_TYPES = {
        "entered_roi",
        "person_entered_roi",
        "person_loitering",
        "crossed_line",
        "person_crossed_line_a_to_b",
        "person_crossed_line_b_to_a",
    }
    CLOSE_TYPES = {
        "left_roi",
        "person_left_roi",
        "person_left",
    }
    LOG_ONLY_TYPES = {
        "person_entered",
    }
    LINE_TYPES = {
        "crossed_line",
        "person_crossed_line_a_to_b",
        "person_crossed_line_b_to_a",
    }
    ROI_TYPES = {
        "entered_roi",
        "person_entered_roi",
        "left_roi",
        "person_left_roi",
        "person_loitering",
    }

    def build_decision(self, camera_id: int, event: dict, rules, db, now: datetime | None = None) -> AlarmLifecycleDecision:
        current_time = now or utc_now_naive()
        event_type = self._normalize_event_type(event.get("event_type"))
        track_id = self._safe_track_id(event.get("track_id"))
        severity = self._infer_severity(event)

        if event_type in self.LOG_ONLY_TYPES:
            return AlarmLifecycleDecision(
                alarm_eligible=False,
                lifecycle_action="log_only",
                alarm_category=self._alarm_category_for(event_type),
                correlation_key=self._build_correlation_key(camera_id, event_type, track_id, rules),
                reason="default_log_only",
                status="closed",
                is_alarm_active=False,
                resolved_at=current_time,
            )

        if event_type in self.OPEN_TYPES:
            related_event = self._find_active_alarm_for_open(
                db=db,
                camera_id=camera_id,
                event_type=event_type,
                track_id=track_id,
                rules=rules,
                now=current_time,
            )
            if related_event is not None:
                return AlarmLifecycleDecision(
                    alarm_eligible=False,
                    lifecycle_action="log_only",
                    alarm_category=self._alarm_category_for(event_type),
                    correlation_key=self._build_correlation_key(camera_id, event_type, track_id, rules, related_event),
                    reason="already_active_alarm",
                    status="closed",
                    is_alarm_active=False,
                    related_event_id=related_event.id,
                    resolved_at=current_time,
                )

            return AlarmLifecycleDecision(
                alarm_eligible=True,
                lifecycle_action="open",
                alarm_category=self._alarm_category_for(event_type),
                correlation_key=self._build_correlation_key(camera_id, event_type, track_id, rules),
                reason="open_alarm_rule",
                status="new",
                is_alarm_active=True,
            )

        if event_type in self.CLOSE_TYPES:
            related_event = self._find_related_active_alarm(
                db=db,
                camera_id=camera_id,
                event_type=event_type,
                track_id=track_id,
                rules=rules,
                now=current_time,
            )
            if related_event is not None:
                correlation_key = self._build_correlation_key(camera_id, event_type, track_id, rules, related_event)
                return AlarmLifecycleDecision(
                    alarm_eligible=False,
                    lifecycle_action="close",
                    alarm_category=self._alarm_category_for(event_type),
                    correlation_key=correlation_key,
                    reason="correlated_close",
                    status="closed",
                    is_alarm_active=False,
                    related_event_id=related_event.id,
                    resolved_at=current_time,
                )

            return AlarmLifecycleDecision(
                alarm_eligible=False,
                lifecycle_action="log_only",
                alarm_category=self._alarm_category_for(event_type),
                correlation_key=self._build_correlation_key(camera_id, event_type, track_id, rules),
                reason="no_correlated_alarm",
                status="closed",
                is_alarm_active=False,
                resolved_at=current_time,
            )

        if severity in {"high", "critical"}:
            return AlarmLifecycleDecision(
                alarm_eligible=True,
                lifecycle_action="open",
                alarm_category="legacy",
                correlation_key=self._build_correlation_key(camera_id, event_type, track_id, rules),
                reason="severity_alarm_rule",
                status="new",
                is_alarm_active=True,
            )

        return AlarmLifecycleDecision(
            alarm_eligible=False,
            lifecycle_action="log_only",
            alarm_category="legacy",
            correlation_key=self._build_correlation_key(camera_id, event_type, track_id, rules),
            reason="below_alarm_threshold",
            status="closed",
            is_alarm_active=False,
            resolved_at=current_time,
        )

    def apply_decision(self, event_obj: Event, decision: AlarmLifecycleDecision, now: datetime | None = None) -> None:
        current_time = now or utc_now_naive()
        event_obj.alarm_eligible = decision.alarm_eligible
        event_obj.lifecycle_action = decision.lifecycle_action
        event_obj.alarm_category = decision.alarm_category
        event_obj.correlation_key = decision.correlation_key
        event_obj.related_event_id = decision.related_event_id
        event_obj.resolved_by_event_id = decision.resolved_by_event_id
        event_obj.resolved_at = decision.resolved_at
        event_obj.is_alarm_active = decision.is_alarm_active
        event_obj.status = decision.status

        if decision.status == "new":
            event_obj.closed_at = None
            if not getattr(event_obj, "acknowledged_at", None):
                event_obj.acknowledged_at = None
        else:
            event_obj.closed_at = current_time
            if not getattr(event_obj, "acknowledged_at", None):
                event_obj.acknowledged_at = current_time

    def finalize_related_alarm(self, db, related_event_id: int | None, resolver_event_id: int | None, now: datetime | None = None):
        if not related_event_id or not resolver_event_id:
            return None

        current_time = now or utc_now_naive()
        related_event = db.query(Event).filter(Event.id == related_event_id).first()
        if not related_event:
            return None

        related_event.status = "closed"
        related_event.is_alarm_active = False
        related_event.resolved_by_event_id = resolver_event_id
        related_event.resolved_at = current_time
        related_event.closed_at = current_time
        related_event.resolution_code = "automatic_clear"
        already_recorded = db.query(IncidentTimeline.id).filter(
            IncidentTimeline.event_id == related_event.id,
            IncidentTimeline.action == "auto_closed",
        ).first()
        if not already_recorded:
            db.add(
                IncidentTimeline(
                    event_id=related_event.id,
                    actor_username="system",
                    action="auto_closed",
                    from_status="new",
                    to_status="closed",
                    comment=f"Fechamento correlacionado pelo evento #{resolver_event_id}.",
                )
            )
        return related_event

    def _find_related_active_alarm(self, db, camera_id: int, event_type: str, track_id: int | None, rules, now: datetime) -> Event | None:
        window = timedelta(seconds=float(settings.alarm_lifecycle_correlation_window_seconds))
        candidates = (
            db.query(Event)
            .filter(
                Event.camera_id == camera_id,
                or_(Event.is_alarm_active.is_(True), Event.status == "new", Event.status == "acknowledged"),
            )
            .order_by(Event.id.desc())
            .limit(50)
            .all()
        )

        preferred_categories = self._preferred_categories_for_close(event_type)
        for candidate in candidates:
            created_at = candidate.created_at
            if created_at is not None and (now - created_at) > window:
                continue

            if track_id is not None and candidate.track_id is not None and int(candidate.track_id) != int(track_id):
                continue

            if preferred_categories and candidate.alarm_category not in preferred_categories:
                continue

            return candidate

        if track_id is None:
            correlation_key = self._build_correlation_key(camera_id, event_type, track_id, rules)
            for candidate in candidates:
                created_at = candidate.created_at
                if created_at is not None and (now - created_at) > window:
                    continue
                if candidate.correlation_key == correlation_key:
                    return candidate

        return None

    def _find_active_alarm_for_open(self, db, camera_id: int, event_type: str, track_id: int | None, rules, now: datetime) -> Event | None:
        window = timedelta(seconds=float(settings.alarm_lifecycle_correlation_window_seconds))
        candidates = (
            db.query(Event)
            .filter(
                Event.camera_id == camera_id,
                or_(Event.is_alarm_active.is_(True), Event.status == "new", Event.status == "acknowledged"),
            )
            .order_by(Event.id.desc())
            .limit(50)
            .all()
        )

        preferred_categories = self._preferred_categories_for_open(event_type)
        correlation_key = self._build_correlation_key(camera_id, event_type, track_id, rules)

        for candidate in candidates:
            created_at = candidate.created_at
            if created_at is not None and (now - created_at) > window:
                continue

            if preferred_categories and candidate.alarm_category not in preferred_categories:
                continue

            if track_id is not None and candidate.track_id is not None and int(candidate.track_id) == int(track_id):
                return candidate

        for candidate in candidates:
            created_at = candidate.created_at
            if created_at is not None and (now - created_at) > window:
                continue

            if preferred_categories and candidate.alarm_category not in preferred_categories:
                continue

            if candidate.correlation_key == correlation_key:
                return candidate

        return None

    def _build_correlation_key(self, camera_id: int, event_type: str, track_id: int | None, rules, related_event: Event | None = None) -> str:
        family = self._family_for(event_type)
        geometry_token = self._geometry_token(rules, related_event)
        track_token = f"track:{track_id}" if track_id is not None else "track:na"
        raw = "|".join([f"camera:{camera_id}", f"family:{family}", track_token, geometry_token])
        return f"camera:{camera_id}|family:{family}|{track_token}|geo:{sha1(geometry_token.encode('utf-8')).hexdigest()[:10]}|sig:{sha1(raw.encode('utf-8')).hexdigest()[:12]}"

    @staticmethod
    def _normalize_event_type(event_type: str | None) -> str:
        return str(event_type or "").strip().lower()

    @staticmethod
    def _safe_track_id(track_id) -> int | None:
        if track_id is None:
            return None
        try:
            return int(track_id)
        except Exception:
            return None

    @staticmethod
    def _infer_severity(event: dict) -> str:
        severity = str(event.get("severity") or "").lower()
        if severity in {"low", "medium", "high", "critical"}:
            return severity

        event_type = str(event.get("event_type") or "").lower()
        if "crossed_line" in event_type:
            return "high"
        if "entered_roi" in event_type:
            return "high"
        if event_type in {"person_entered", "person_left"}:
            return "medium"
        if "left_roi" in event_type:
            return "low"
        return "medium"

    @staticmethod
    def _alarm_category_for(event_type: str) -> str | None:
        if event_type in {"entered_roi", "person_entered_roi", "left_roi", "person_left_roi", "person_loitering"}:
            return "roi"
        if event_type in {"crossed_line", "person_crossed_line_a_to_b", "person_crossed_line_b_to_a"}:
            return "line"
        if event_type in {"person_entered", "person_left"}:
            return "presence"
        return "legacy"

    @staticmethod
    def _preferred_categories_for_close(event_type: str) -> set[str]:
        if event_type in {"left_roi", "person_left_roi"}:
            return {"roi"}
        if event_type == "person_left":
            return {"roi", "line", "presence", "legacy"}
        return {"roi", "line", "presence", "legacy"}

    @staticmethod
    def _preferred_categories_for_open(event_type: str) -> set[str]:
        if event_type in {"entered_roi", "person_entered_roi", "person_loitering"}:
            return {"roi"}
        if event_type in {"crossed_line", "person_crossed_line_a_to_b", "person_crossed_line_b_to_a"}:
            return {"line"}
        if event_type in {"person_entered", "person_left"}:
            return {"presence"}
        return {"roi", "line", "presence", "legacy"}

    @staticmethod
    def _family_for(event_type: str) -> str:
        if event_type in {"entered_roi", "person_entered_roi", "left_roi", "person_left_roi"}:
            return "roi"
        if event_type in {"crossed_line", "person_crossed_line_a_to_b", "person_crossed_line_b_to_a"}:
            return "line"
        if event_type in {"person_entered", "person_left"}:
            return "presence"
        return "legacy"

    @staticmethod
    def _geometry_token(rules, related_event: Event | None = None) -> str:
        roi = getattr(rules, "roi_polygon", None) or []
        line = getattr(rules, "line", None)
        line_direction = str(getattr(rules, "line_direction", "any") or "any")
        tokens: list[str] = [f"dir:{line_direction}"]

        if roi:
            roi_tokens = []
            for point in roi:
                if not point or len(point) != 2:
                    continue
                try:
                    roi_tokens.append(f"{int(point[0])}:{int(point[1])}")
                except Exception:
                    continue
            tokens.append("roi:" + ";".join(roi_tokens) if roi_tokens else "roi:na")
        else:
            tokens.append("roi:na")

        if line and len(line) == 2:
            try:
                start = line[0]
                end = line[1]
                tokens.append(f"line:{int(start[0])}:{int(start[1])}:{int(end[0])}:{int(end[1])}")
            except Exception:
                tokens.append("line:na")
        else:
            tokens.append("line:na")

        if related_event and related_event.correlation_key:
            tokens.append(f"related:{related_event.correlation_key}")

        return "|".join(tokens)


alarm_lifecycle_service = AlarmLifecycleService()
