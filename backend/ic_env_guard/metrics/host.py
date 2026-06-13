import psutil
from prometheus_client import CollectorRegistry


def update_host_metrics(registry: CollectorRegistry) -> None:
    names = registry._names_to_collectors  # prometheus_client has no public lookup helper
    names["ic_env_guard_host_cpu_percent"].set(psutil.cpu_percent(interval=None))
    memory = psutil.virtual_memory()
    names["ic_env_guard_host_memory_used_bytes"].set(memory.used)
    names["ic_env_guard_host_memory_total_bytes"].set(memory.total)
    disk = psutil.disk_usage("/")
    names["ic_env_guard_host_disk_used_bytes"].labels(mount="/").set(disk.used)
    names["ic_env_guard_host_disk_total_bytes"].labels(mount="/").set(disk.total)
