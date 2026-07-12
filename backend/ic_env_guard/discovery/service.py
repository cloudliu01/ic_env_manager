import asyncio
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from time import monotonic
from uuid import uuid4

from ic_env_guard.config.models import DiscoveryConfig
from ic_env_guard.discovery.fingerprint import DiscoveryProbeError
from ic_env_guard.discovery.models import DiscoveryJob, DiscoveryState, DiscoveryTarget
from ic_env_guard.discovery.ports import DiscoveryFingerprinter
from ic_env_guard.fleet.models import RegistryConflict
from ic_env_guard.fleet.transport import TransportProfile, TrustedLanHttpProfile
from ic_env_guard.storage.discovery import DiscoveryRepository

_SAFE_PROBE_ERRORS = {
    "timeout",
    "network_error",
    "fingerprint_too_large",
    "transport_profile_unknown",
    "transport_profile_mismatch",
}


class DiscoveryService:
    def __init__(
        self,
        *,
        config: DiscoveryConfig,
        transport_profiles: tuple[TransportProfile, ...],
        repository: DiscoveryRepository,
        fingerprinter: DiscoveryFingerprinter,
        clock=None,
        outcome_recorder=None,
        registry=None,
        enrollment_journal=None,
        self_targets=(),
        available: bool = True,
    ) -> None:
        self.config = config
        self.repository = repository
        self.fingerprinter = fingerprinter
        self._clock = clock or (lambda: datetime.now(UTC))
        self._outcome_recorder = outcome_recorder
        self._registry = registry
        self._enrollment_journal = enrollment_journal
        self._self_targets = {ip_address(value).compressed for value in self_targets}
        self._profiles = {profile.id: profile for profile in transport_profiles}
        self._scopes = {scope.id: scope for scope in config.scopes} if available else {}
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._tasks: dict[str, asyncio.Task] = {}
        self._closing = False

    def start(self, scope_id: str, *, start_audit_event_id: int) -> DiscoveryJob:
        if self._closing:
            raise RegistryConflict("discovery_unavailable")
        scope = self._scopes.get(scope_id)
        if scope is None:
            raise RegistryConflict(
                "discovery_disabled" if not self._scopes else "discovery_scope_not_found"
            )
        for expired in self.repository.expire_deadlines(now=self._clock()):
            if self._outcome_recorder is not None:
                self._outcome_recorder(expired)
        targets = self._targets(scope)
        if len(targets) > self.config.max_targets:
            raise RegistryConflict("discovery_target_limit")
        now = self._clock()
        job = self.repository.create_job(
            DiscoveryJob(
                job_id=str(uuid4()), scope_id=scope_id,
                state=DiscoveryState.QUEUED, total_targets=len(targets),
                checked_targets=0, found_targets=0, cancel_requested=False,
                safe_error_code=None, start_audit_event_id=start_audit_event_id,
                deadline_at=now + timedelta(seconds=self.config.job_timeout_seconds),
                created_at=now, updated_at=now,
            )
        )
        self._tasks[job.job_id] = asyncio.create_task(self._run(job.job_id, targets))
        self._tasks[job.job_id].add_done_callback(
            lambda _task, job_id=job.job_id: self._tasks.pop(job_id, None)
        )
        return job

    async def cancel(self, job_id: str) -> DiscoveryJob:
        now = self._clock()
        self.repository.request_cancel(job_id, now=now)
        task = self._tasks.get(job_id)
        if task is not None:
            task.cancel()
            await asyncio.wait(
                {task}, timeout=min(2.0, self.config.fingerprint_timeout_seconds)
            )
        finished = self.repository.finish(
            job_id, DiscoveryState.CANCELLED, now=self._clock()
        )
        if self._outcome_recorder is not None:
            self._outcome_recorder(finished)
        return finished

    def scopes(self):
        return tuple(self._scopes.values())

    def target_count(self, scope) -> int:
        return sum(1 for _target in self._iter_scope_targets(scope))

    def get(self, job_id: str) -> DiscoveryJob | None:
        return self.repository.get_job(job_id)

    def results(self, job_id: str):
        if self.repository.get_job(job_id) is None:
            raise RegistryConflict("discovery_job_not_found")
        return self.repository.list_results(job_id)

    def classify(self, result) -> tuple[str, str | None]:
        if (
            result.found
            and self._registry is not None
            and self._registry.find_duplicate(
                instance_id=None, normalized_endpoint=result.canonical_url
            )
            is not None
        ):
            return "already_registered", "already_registered"
        if result.linked_enrollment_id and self._enrollment_journal is not None:
            enrollment = self._enrollment_journal.get(result.linked_enrollment_id)
            if enrollment is not None:
                if enrollment.state.value == "verified":
                    return "new", "verified"
                if enrollment.state.value == "consumed":
                    return "already_registered", "already_registered"
                if not enrollment.state.terminal:
                    return "new", "enrolling"
        return (
            ("new", "enrollment_required")
            if result.found
            else ("unavailable", None)
        )

    async def wait(self, job_id: str) -> None:
        task = self._tasks.get(job_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        self._closing = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def recover_and_cleanup(self) -> None:
        now = self._clock()
        resumable, terminal = self.repository.recover(now=now)
        for job in terminal:
            if self._outcome_recorder is not None:
                self._outcome_recorder(job)
        if self._outcome_recorder is not None:
            for job in self.repository.terminal_jobs_with_pending_audit():
                self._outcome_recorder(job)
        self.repository.cleanup(
            retained_after=now - timedelta(seconds=self.config.retention_seconds)
        )
        for job in resumable:
            scope = self._scopes.get(job.scope_id)
            if scope is None:
                finished = self.repository.finish(
                    job.job_id,
                    DiscoveryState.FAILED,
                    now=now,
                    safe_error_code="scope_unavailable",
                )
                if self._outcome_recorder is not None:
                    self._outcome_recorder(finished)
                continue
            targets = self._targets(scope)
            self._tasks[job.job_id] = asyncio.create_task(
                self._run(job.job_id, targets)
            )
            self._tasks[job.job_id].add_done_callback(
                lambda _task, job_id=job.job_id: self._tasks.pop(job_id, None)
            )

    def _iter_scope_targets(self, scope):
        for address in scope.cidr.hosts():
            canonical_ip = ip_address(address).compressed
            for endpoint in scope.endpoints:
                profile = self._profiles[endpoint.transport_profile_id]
                yield DiscoveryTarget(
                    ip=canonical_ip,
                    port=endpoint.port,
                    transport_profile_id=profile.id,
                    scheme=(
                        "http"
                        if isinstance(profile, TrustedLanHttpProfile)
                        else "https"
                    ),
                )

    def _targets(self, scope) -> tuple[DiscoveryTarget, ...]:
        return tuple(self._iter_scope_targets(scope))

    async def _run(self, job_id: str, targets: tuple[DiscoveryTarget, ...]) -> None:
        current = self.repository.claim(job_id, now=self._clock())
        if current is None:
            return
        checked = self.repository.result_keys(job_id)
        targets = tuple(
            target
            for target in targets
            if (target.ip, target.port, target.transport_profile_id) not in checked
        )
        deadline = monotonic() + max(
            0.0, (current.deadline_at - self._clock()).total_seconds()
        )
        queue: asyncio.Queue[DiscoveryTarget] = asyncio.Queue(maxsize=len(targets) or 1)
        for target in targets:
            queue.put_nowait(target)

        async def worker() -> None:
            while not queue.empty():
                job = self.repository.get_job(job_id)
                if job is None or job.cancel_requested or monotonic() >= deadline:
                    return
                target = queue.get_nowait()
                fingerprint = None
                error = None
                if target.ip in self._self_targets:
                    self.repository.record_result(
                        job_id,
                        target,
                        None,
                        "self_target_forbidden",
                        now=self._clock(),
                    )
                    continue
                profile = self._profiles.get(target.transport_profile_id)
                expected_scheme = (
                    "http" if isinstance(profile, TrustedLanHttpProfile) else "https"
                )
                if profile is None or target.scheme != expected_scheme:
                    self.repository.record_result(
                        job_id,
                        target,
                        None,
                        "transport_profile_mismatch",
                        now=self._clock(),
                    )
                    continue
                try:
                    async with self._semaphore:
                        self.repository.merge_dispatch(
                            job_id, "unknown", now=self._clock()
                        )
                        fingerprint = await asyncio.wait_for(
                            self.fingerprinter.probe(
                                target,
                                connect_timeout=self.config.connect_timeout_ms / 1000,
                                fingerprint_timeout=self.config.fingerprint_timeout_seconds,
                            ),
                            timeout=min(
                                self.config.fingerprint_timeout_seconds,
                                max(0.001, deadline - monotonic()),
                            ),
                        )
                        self.repository.merge_dispatch(
                            job_id, "dispatched", now=self._clock()
                        )
                    if fingerprint is None:
                        error = "fingerprint_mismatch"
                except TimeoutError:
                    error = "timeout"
                except DiscoveryProbeError as exc:
                    if exc.code in {"network_error", "fingerprint_too_large"}:
                        self.repository.merge_dispatch(
                            job_id, "dispatched", now=self._clock()
                        )
                    error = (
                        exc.code
                        if exc.code in _SAFE_PROBE_ERRORS
                        else "fingerprint_error"
                    )
                except Exception:
                    error = "network_error"
                self.repository.record_result(
                    job_id, target, fingerprint, error, now=self._clock()
                )

        workers = [
            asyncio.create_task(worker())
            for _ in range(min(self.config.max_concurrency, len(targets)))
        ]
        await asyncio.gather(*workers, return_exceptions=True)
        latest = self.repository.get_job(job_id)
        if latest is None:
            return
        if latest.cancel_requested:
            state, error = DiscoveryState.CANCELLED, None
        elif monotonic() >= deadline and latest.checked_targets < latest.total_targets:
            state, error = DiscoveryState.FAILED, "job_timeout"
        else:
            state, error = DiscoveryState.COMPLETED, None
        finished = self.repository.finish(
            job_id, state, now=self._clock(), safe_error_code=error
        )
        if self._outcome_recorder is not None and finished.state in {
            DiscoveryState.COMPLETED,
            DiscoveryState.CANCELLED,
            DiscoveryState.FAILED,
        }:
            self._outcome_recorder(finished)
