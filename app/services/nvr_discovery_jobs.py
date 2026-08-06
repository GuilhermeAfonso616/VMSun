from __future__ import annotations

import secrets
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from threading import Lock, Thread
from typing import Any

from app.camera.rtsp_discovery import probe_rtsp_url_details_bounded
from app.video_sources.models import StreamProfile


JOB_TTL_SECONDS = 30 * 60
DEFAULT_MAX_WORKERS = 4
DEFAULT_PROBE_TIMEOUT_SECONDS = 15.0


def probe_worker_count(form_values: dict[str, Any]) -> int:
    # Dahua/Intelbras devices commonly enforce a small RTSP session/login
    # budget. Discovery must not compete with the already-running cameras.
    brand = str(form_values.get("brand") or "").strip().lower()
    return 1 if brand in {"dahua", "intelbras"} else DEFAULT_MAX_WORKERS


@dataclass(slots=True)
class NvrDiscoveryCandidate:
    profile: StreamProfile
    status: str = "pending"
    attempts: int = 0
    started_at: float | None = None
    completed_at: float | None = None


@dataclass(slots=True)
class NvrDiscoveryJob:
    token: str
    owner_user_id: int
    host: str
    username: str
    password: str
    form_values: dict[str, Any]
    probe_enabled: bool
    candidates: list[NvrDiscoveryCandidate]
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + JOB_TTL_SECONDS)
    status: str = "queued"
    last_error: str = ""
    run_generation: int = 0
    lock: Lock = field(default_factory=Lock, repr=False)

    def touch(self) -> None:
        self.updated_at = time.time()
        self.expires_at = self.updated_at + JOB_TTL_SECONDS

    def counts(self) -> dict[str, int]:
        counts = {key: 0 for key in ("pending", "running", "ok", "failed", "timeout", "not_tested")}
        for candidate in self.candidates:
            counts[candidate.status] = counts.get(candidate.status, 0) + 1
        counts["total"] = len(self.candidates)
        counts["completed"] = counts["ok"] + counts["failed"] + counts["timeout"] + counts["not_tested"]
        counts["resumable"] = counts["timeout"] + counts["pending"]
        return counts

    def public_snapshot(self) -> dict[str, Any]:
        with self.lock:
            self.touch()
            counts = self.counts()
            total = max(1, counts["total"])
            percent = 100 if self.status == "completed" else min(99, int((counts["completed"] / total) * 100))
            running = [
                {
                    "channel": item.profile.channel,
                    "stream_kind": item.profile.stream_kind,
                }
                for item in self.candidates
                if item.status == "running"
            ]
            discovered = [
                {
                    "channel": item.profile.channel,
                    "stream_kind": item.profile.stream_kind,
                    "name": item.profile.name,
                    "ok": item.profile.ok,
                    "status": item.status,
                    "error": item.profile.error,
                    "width": item.profile.width,
                    "height": item.profile.height,
                }
                for item in self.candidates
                if item.status in {"ok", "failed", "timeout"}
            ]
            return {
                "token": self.token,
                "status": self.status,
                "counts": counts,
                "percent": percent,
                "running": running,
                "discovered": discovered,
                "last_error": self.last_error,
                "result_url": f"/video-sources/nvr/discover/{self.token}/result" if self.status == "completed" else None,
            }

    def profile_snapshot(self) -> list[StreamProfile]:
        with self.lock:
            return [candidate.profile for candidate in self.candidates]


class NvrDiscoveryJobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, NvrDiscoveryJob] = {}
        self._lock = Lock()

    def _cleanup(self) -> None:
        now = time.time()
        with self._lock:
            expired = [
                token
                for token, job in self._jobs.items()
                if job.expires_at < now and job.status not in {"queued", "running"}
            ]
            for token in expired:
                self._jobs.pop(token, None)

    def create(
        self,
        *,
        owner_user_id: int,
        host: str,
        username: str,
        password: str,
        form_values: dict[str, Any],
        profiles: list[StreamProfile],
        probe_enabled: bool,
    ) -> NvrDiscoveryJob:
        self._cleanup()
        token = secrets.token_urlsafe(24)
        candidates = [
            NvrDiscoveryCandidate(
                profile=replace(profile, ok=not probe_enabled, error=""),
                status="pending" if probe_enabled else "not_tested",
            )
            for profile in profiles
        ]
        job = NvrDiscoveryJob(
            token=token,
            owner_user_id=int(owner_user_id),
            host=host,
            username=username,
            password=password,
            form_values=dict(form_values),
            probe_enabled=bool(probe_enabled),
            candidates=candidates,
            status="queued" if probe_enabled else "completed",
        )
        with self._lock:
            self._jobs[token] = job
        if probe_enabled:
            self._start(job)
        return job

    def get(self, token: str, *, owner_user_id: int) -> NvrDiscoveryJob | None:
        self._cleanup()
        with self._lock:
            job = self._jobs.get(str(token or ""))
        if not job or int(job.owner_user_id) != int(owner_user_id):
            return None
        with job.lock:
            job.touch()
        return job

    def pause(self, token: str, *, owner_user_id: int) -> NvrDiscoveryJob | None:
        job = self.get(token, owner_user_id=owner_user_id)
        if not job:
            return None
        with job.lock:
            if job.status in {"queued", "running"}:
                job.run_generation += 1
                job.status = "paused"
                for candidate in job.candidates:
                    if candidate.status == "running":
                        candidate.status = "pending"
                        candidate.started_at = None
                job.touch()
        return job

    def resume(self, token: str, *, owner_user_id: int) -> NvrDiscoveryJob | None:
        job = self.get(token, owner_user_id=owner_user_id)
        if not job:
            return None
        with job.lock:
            if job.status in {"queued", "running"}:
                return job
            for candidate in job.candidates:
                if candidate.status in {"timeout", "pending", "running"}:
                    candidate.status = "pending"
                    candidate.started_at = None
                    candidate.completed_at = None
                    candidate.profile = replace(candidate.profile, ok=False, error="")
            if not any(candidate.status == "pending" for candidate in job.candidates):
                return job
            job.status = "queued"
            job.last_error = ""
            job.touch()
        self._start(job)
        return job

    def _start(self, job: NvrDiscoveryJob) -> None:
        with job.lock:
            job.run_generation += 1
            generation = job.run_generation
        Thread(
            target=self._run,
            args=(job, generation),
            name=f"nvr-discovery-{job.token[:8]}",
            daemon=True,
        ).start()

    @staticmethod
    def _probe(candidate: NvrDiscoveryCandidate) -> dict[str, Any]:
        return probe_rtsp_url_details_bounded(
            candidate.profile.rtsp_url,
            timeout_seconds=DEFAULT_PROBE_TIMEOUT_SECONDS,
            transport="tcp",
        )

    def _probe_candidate(
        self,
        job: NvrDiscoveryJob,
        generation: int,
        index: int,
    ) -> dict[str, Any]:
        with job.lock:
            if generation != job.run_generation:
                return {"ok": False, "error": "Execucao substituida", "timed_out": False}
            candidate = job.candidates[index]
            candidate.status = "running"
            candidate.attempts += 1
            candidate.started_at = time.time()
            job.touch()
        return self._probe(candidate)

    def _run(self, job: NvrDiscoveryJob, generation: int) -> None:
        try:
            with job.lock:
                if generation != job.run_generation:
                    return
                pending_indexes = [index for index, item in enumerate(job.candidates) if item.status == "pending"]
                job.status = "running"
                job.touch()

            with ThreadPoolExecutor(
                max_workers=probe_worker_count(job.form_values),
                thread_name_prefix="nvr-probe",
            ) as executor:
                future_to_index = {
                    executor.submit(self._probe_candidate, job, generation, index): index
                    for index in pending_indexes
                }

                for future in as_completed(future_to_index):
                    index = future_to_index[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {
                            "ok": False,
                            "error": f"Falha inesperada no probe: {exc.__class__.__name__}",
                            "timed_out": False,
                        }

                    with job.lock:
                        if generation != job.run_generation:
                            return
                        candidate = job.candidates[index]
                        ok = bool(result.get("ok"))
                        timed_out = bool(result.get("timed_out"))
                        metadata = dict(candidate.profile.metadata or {})
                        metadata["fps"] = result.get("fps")
                        metadata["probe_attempts"] = candidate.attempts
                        candidate.profile = replace(
                            candidate.profile,
                            ok=ok,
                            error=str(result.get("error") or ""),
                            width=int(result.get("width") or 0) or None,
                            height=int(result.get("height") or 0) or None,
                            metadata=metadata,
                        )
                        candidate.status = "ok" if ok else ("timeout" if timed_out else "failed")
                        candidate.completed_at = time.time()
                        job.touch()

            with job.lock:
                if generation == job.run_generation:
                    job.status = "completed"
                    job.touch()
        except Exception as exc:
            with job.lock:
                for candidate in job.candidates:
                    if candidate.status == "running":
                        candidate.status = "pending"
                        candidate.started_at = None
                job.status = "interrupted"
                job.last_error = f"{exc.__class__.__name__}: {exc}"
                job.touch()


nvr_discovery_job_manager = NvrDiscoveryJobManager()
