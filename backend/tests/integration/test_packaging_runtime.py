import importlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from ic_env_guard.auth.token import generate_bearer_token, validate_token_file_permissions


@pytest.mark.integration
def test_generated_token_file_permissions_are_owner_only(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text(generate_bearer_token(), encoding="utf-8")
    token_file.chmod(0o600)

    validate_token_file_permissions(token_file)


@pytest.mark.integration
def test_runtime_documentation_declares_controlled_runtime():
    readme = Path(__file__).resolve().parents[3] / "packaging" / "runtime" / "README.md"
    if not readme.exists():
        pytest.skip("runtime README not created yet")
    text = readme.read_text(encoding="utf-8")
    assert "controlled Python runtime" in text
    assert "system Python" in text


@pytest.mark.integration
def test_agent_migrations_are_packaged_with_runtime_code():
    migrations_package = importlib.import_module("ic_env_guard.migrations")
    migrations_path = Path(migrations_package.__file__).parent

    assert migrations_path.is_dir()
    assert (migrations_path / "0001_initial.py").is_file()


@pytest.mark.integration
def test_control_plane_runtime_dependencies_are_declared():
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = pyproject["project"]["dependencies"]
    test_dependencies = pyproject["project"]["optional-dependencies"]["test"]

    assert any(dependency.startswith("httpx>=") for dependency in dependencies)
    assert any(dependency.startswith("websockets>=") for dependency in dependencies)
    assert any(dependency.startswith("build>=1.2") for dependency in test_dependencies)


@pytest.mark.integration
@pytest.mark.security
def test_start_script_disables_proxy_header_client_rewriting(tmp_path):
    project_root = Path(__file__).resolve().parents[3]
    stubs = tmp_path / "stubs"
    package = stubs / "ic_env_guard"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "main.py").write_text(
        "def create_app(config_path=None):\n    return object()\n", encoding="utf-8"
    )
    (stubs / "sitecustomize.py").write_text(
        "import asyncio, json, os\n"
        "import uvicorn\n"
        "from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware\n"
        "def run(app, **kwargs):\n"
        "    observed = {}\n"
        "    async def target(scope, receive, send):\n"
        "        observed['client'] = scope['client']\n"
        "    configured = target\n"
        "    if kwargs.get('proxy_headers', True):\n"
        "        configured = ProxyHeadersMiddleware(target, trusted_hosts='*')\n"
        "    scope = {\n"
        "        'type': 'http', 'client': ('127.0.0.1', 12345),\n"
        "        'headers': [(b'x-forwarded-for', b'198.51.100.77')],\n"
        "    }\n"
        "    async def receive(): return {'type': 'http.disconnect'}\n"
        "    async def send(message): pass\n"
        "    asyncio.run(configured(scope, receive, send))\n"
        "    with open(os.environ['UVICORN_CAPTURE'], 'w') as stream:\n"
        "        json.dump({'kwargs': kwargs, 'client': observed['client']}, stream)\n"
        "uvicorn.run = run\n",
        encoding="utf-8",
    )
    wrapper_dir = tmp_path / "bin"
    wrapper_dir.mkdir()
    python_wrapper = wrapper_dir / "python"
    python_wrapper.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = \"-m\" ]; then exit 0; fi\n"
        "cd \"$START_STUB_DIR\"\n"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    python_wrapper.chmod(0o700)
    dev_dir = tmp_path / "dev"
    dev_dir.mkdir()
    token_file = dev_dir / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config_file = dev_dir / "config.yaml"
    config_file.write_text("mode: agent\nstate_database: /tmp/state.db\n", encoding="utf-8")
    capture = tmp_path / "uvicorn.json"
    environment = os.environ.copy()
    environment.update(
        {
            "CONDA_DEFAULT_ENV": "venv312",
            "SKIP_INSTALL": "1",
            "IC_ENV_GUARD_DEV_DIR": str(dev_dir),
            "IC_ENV_GUARD_TOKEN_FILE": str(token_file),
            "IC_ENV_GUARD_CONFIG": str(config_file),
            "START_STUB_DIR": str(stubs),
            "UVICORN_CAPTURE": str(capture),
            "PATH": f"{wrapper_dir}:{environment['PATH']}",
            "PYTHONPATH": str(stubs),
        }
    )

    subprocess.run(
        [str(project_root / "start.sh"), "backend"],
        cwd=project_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    observed = json.loads(capture.read_text(encoding="utf-8"))
    assert observed["kwargs"]["proxy_headers"] is False
    assert observed["client"][0] == "127.0.0.1"
