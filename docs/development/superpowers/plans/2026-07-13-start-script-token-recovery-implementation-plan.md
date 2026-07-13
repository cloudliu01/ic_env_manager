# Start Script Development Token Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `start.sh` atomically repair missing or blank development bearer-token files while preserving every valid existing token.

**Architecture:** Keep credential creation inside the local development launcher. A single shell helper classifies a token as valid only when its regular file contains non-whitespace data, writes replacements to a private sibling temporary file, and atomically moves the completed value into place. Existing runtime token loading, production packaging, configuration structure, and process orchestration remain unchanged.

**Tech Stack:** Bash 3.2-compatible shell, Python 3.12 standard library, pytest integration tests, Conda environment `venv312`.

## Global Constraints

- Limit production changes to `start.sh`; do not change backend runtime authentication behavior.
- Preserve non-blank token contents exactly across repeated commands.
- Recover missing, zero-byte, and whitespace-only development token files.
- Generate `secrets.token_urlsafe(32)` values without printing them.
- Set replacement token permissions to `0600` before atomic installation.
- Keep Agent and Manager configuration paths, ports, and lifecycle unchanged.
- Do not modify or stage the user's unrelated main-worktree files: `CLAUDE.md`, `.kilo/`, or `AGENTS.md`.

---

### Task 1: Recover blank development tokens atomically

**Files:**
- Modify: `backend/tests/integration/test_manager_restart_recovery.py:25-60`
- Modify: `start.sh:61-96`

**Interfaces:**
- Consumes: `start.sh config <mode>`, `IC_ENV_GUARD_DEV_DIR`, `SKIP_INSTALL`, and the existing `venv312` test environment.
- Produces: Bash function `ensure_dev_token <token_path>`; an existing non-blank regular file is preserved, while missing or blank input is replaced by an owner-only generated token.

- [ ] **Step 1: Write the failing regression test**

Replace the existing Manager development configuration test declaration and setup with a parameterized blank-token setup. Keep its existing configuration assertions, then add token recovery and restart preservation assertions:

```python
@pytest.mark.integration
@pytest.mark.parametrize("initial_token", ["", " \n"], ids=["zero-byte", "whitespace-only"])
def test_manager_development_config_repairs_blank_token_and_is_restartable(
    tmp_path, initial_token
):
    token_file = tmp_path / "control-plane.token"
    token_file.write_text(initial_token, encoding="utf-8")
    token_file.chmod(0o600)
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    (executable_dir / "python").symlink_to(Path(sys.executable))
    environment = os.environ | {
        "CONDA_DEFAULT_ENV": "venv312",
        "SKIP_INSTALL": "1",
        "IC_ENV_GUARD_DEV_DIR": str(tmp_path),
        "PATH": f"{executable_dir}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        [str(PROJECT_ROOT / "start.sh"), "config", "control-plane"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    generated_token = token_file.read_text(encoding="utf-8")
    assert generated_token.strip()
    assert token_file.stat().st_mode & 0o777 == 0o600
    config = yaml.safe_load((tmp_path / "control-plane.yaml").read_text())
    control_plane = config["control_plane"]
    assert config["mode"] == "control-plane"
    assert control_plane["audit_database"] == str(tmp_path / "control-plane.db")
    assert control_plane["credential_directory"] == str(tmp_path / "manager-credentials")
    assert control_plane["allowed_agent_cidrs"] == ["127.0.0.0/8"]
    assert control_plane["transport_profiles"] == []
    assert control_plane["discovery"] == {"scopes": []}
    assert config["enrollment"]["manager_socket_path"] == str(
        tmp_path / "manager-enrollment.sock"
    )
    assert "ingest" not in config

    restart = subprocess.run(
        [str(PROJECT_ROOT / "start.sh"), "config", "control-plane"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert restart.returncode == 0, restart.stdout + restart.stderr
    assert token_file.read_text(encoding="utf-8") == generated_token
```

- [ ] **Step 2: Run the regression test and verify RED**

Run:

```bash
cd backend
PYTHONPATH=. conda run -n venv312 pytest -q \
  tests/integration/test_manager_restart_recovery.py::test_manager_development_config_repairs_blank_token_and_is_restartable
```

Expected: both parameter cases fail at `assert generated_token.strip()` because the current script preserves the blank file.

- [ ] **Step 3: Add the minimal atomic token helper**

Insert this function after `activate_backend_env` in `start.sh`:

```bash
ensure_dev_token() {
  local token_path="$1"
  local temporary_path

  if [[ -f "${token_path}" ]] && grep -q '[^[:space:]]' "${token_path}"; then
    return
  fi

  umask 077
  temporary_path="$(mktemp "${token_path}.tmp.XXXXXX")"
  if ! python - <<'PY' > "${temporary_path}"
import secrets
print(secrets.token_urlsafe(32))
PY
  then
    rm -f "${temporary_path}"
    return 1
  fi
  if ! chmod 0600 "${temporary_path}"; then
    rm -f "${temporary_path}"
    return 1
  fi
  if ! mv -f "${temporary_path}" "${token_path}"; then
    rm -f "${temporary_path}"
    return 1
  fi
}
```

Replace both direct token-generation blocks at the start of `ensure_dev_config` with:

```bash
  ensure_dev_token "${TOKEN_FILE}"

  if [[ "${DEV_CONFIG_MODE}" == "control-plane" ]]; then
    ensure_dev_token "${AGENT_TOKEN_FILE}"
  fi
```

Do not change configuration generation or backend runtime code.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
cd backend
PYTHONPATH=. conda run -n venv312 pytest -q \
  tests/integration/test_manager_restart_recovery.py::test_manager_development_config_repairs_blank_token_and_is_restartable
```

Expected: `2 passed`.

- [ ] **Step 5: Run the complete start-script lifecycle test file**

Run:

```bash
cd backend
PYTHONPATH=. conda run -n venv312 pytest -q \
  tests/integration/test_manager_restart_recovery.py
```

Expected: all parameterized configuration cases and the bounded Agent/Manager `start.sh all` lifecycle smoke test pass. The smoke test must observe HTTP 200 from both `127.0.0.1:8765/healthz` and `127.0.0.1:8766/healthz`, then terminate the process group.

- [ ] **Step 6: Validate each standalone development configuration mode**

Run:

```bash
env IC_ENV_GUARD_DEV_DIR=/tmp/ic-env-guard-token-recovery-agent \
  SKIP_INSTALL=1 ./start.sh config agent
env IC_ENV_GUARD_DEV_DIR=/tmp/ic-env-guard-token-recovery-manager \
  SKIP_INSTALL=1 ./start.sh config control-plane
```

Expected: both commands print `configuration valid`; Agent reports Public `127.0.0.1:8765` and Ingest `127.0.0.1:8766`, while Manager reports Public `127.0.0.1:8765`.

- [ ] **Step 7: Run full backend regression and static checks**

Run:

```bash
cd backend
PYTHONPATH=. conda run -n venv312 pytest -q
conda run -n venv312 ruff check .
cd ..
git diff --check
```

Expected: the full pytest suite passes, Ruff reports `All checks passed!`, and `git diff --check` produces no output.

- [ ] **Step 8: Review and commit the implementation**

Confirm the diff contains only the regression test and minimal launcher change, then run:

```bash
git diff -- start.sh backend/tests/integration/test_manager_restart_recovery.py
git add start.sh backend/tests/integration/test_manager_restart_recovery.py
git commit -m "fix: recover blank development tokens"
```

Expected: one implementation commit after the already committed design and plan documents; no unrelated files are staged.

## Final Review Checklist

- [ ] The regression test was observed failing before `start.sh` changed.
- [ ] Zero-byte and whitespace-only Manager tokens are repaired.
- [ ] A second invocation preserves the generated token byte-for-byte.
- [ ] Generated token permissions are `0600`.
- [ ] The same helper is used for runtime and Manager-to-Agent development tokens.
- [ ] `start.sh all` reaches both Agent and Manager health endpoints.
- [ ] No backend production authentication code changed.
- [ ] Full pytest and Ruff checks pass.
