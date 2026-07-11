from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


def create_registry() -> CollectorRegistry:
    registry = CollectorRegistry(auto_describe=True)
    Gauge("ic_env_guard_build_info", "Agent build info", ["version"], registry=registry).labels(
        version="0.1.0"
    ).set(1)
    Gauge(
        "ic_env_guard_websocket_connections", "Active WebSocket connections", registry=registry
    ).set(0)
    Gauge(
        "ic_env_guard_terminal_sessions",
        "Terminal sessions by status",
        ["status"],
        registry=registry,
    )
    Counter(
        "ic_env_guard_api_requests_total",
        "API requests",
        ["method", "route_group", "status_class"],
        registry=registry,
    )
    Gauge("ic_env_guard_host_cpu_percent", "Host CPU percent", registry=registry)
    Gauge("ic_env_guard_host_memory_used_bytes", "Host memory used", registry=registry)
    Gauge("ic_env_guard_host_memory_total_bytes", "Host memory total", registry=registry)
    Gauge("ic_env_guard_host_disk_used_bytes", "Host disk used", ["mount"], registry=registry)
    Gauge("ic_env_guard_host_disk_total_bytes", "Host disk total", ["mount"], registry=registry)
    Counter(
        "ic_env_guard_host_network_rx_bytes_total",
        "Network receive bytes",
        ["interface"],
        registry=registry,
    )
    Counter(
        "ic_env_guard_host_network_tx_bytes_total",
        "Network transmit bytes",
        ["interface"],
        registry=registry,
    )
    Gauge("ic_env_guard_service_up", "Managed service up", ["service"], registry=registry)
    Counter(
        "ic_env_guard_service_restart_total",
        "Managed service restarts",
        ["service"],
        registry=registry,
    )
    Gauge(
        "ic_env_guard_service_start_time_seconds",
        "Managed service start timestamp",
        ["service"],
        registry=registry,
    )
    Gauge(
        "ic_env_guard_service_healthcheck_success",
        "Last healthcheck success",
        ["service"],
        registry=registry,
    )
    Histogram(
        "ic_env_guard_service_healthcheck_latency_seconds",
        "Healthcheck latency",
        ["service"],
        registry=registry,
    )
    Counter(
        "ic_env_guard_cleanup_failures_total",
        "Expiration cleanup failures",
        ["resource"],
        registry=registry,
    )
    return registry
