# Backend and Frontend Test Results

Last local validation: 2026-06-13 on macOS Darwin 25.4.0 using Conda environment `venv312`.

## Backend contract and integration tests

Command:

```bash
cd backend
conda run -n venv312 pytest -q
```

Result:

```text
59 passed, 12 warnings in 5.01s
```

Notes:

- The warnings are Python PTY fork warnings emitted by terminal lifecycle tests in the local macOS developer environment.
- Dedicated Linux platform smoke validation remains documented separately in `platform-validation.md` because systemd behavior must be verified on the supported Linux distributions.
- Ubuntu 24.04, CentOS 7, and RHEL 8 systemd smoke runs were not executed in this macOS workspace.

## Backend lint

Command:

```bash
cd backend
conda run -n venv312 python -m ruff check .
```

Result:

```text
All checks passed!
```

## Frontend tests

Command:

```bash
cd frontend
npm test -- --run
```

Result:

```text
Test Files  4 passed (4)
Tests       4 passed (4)
```

## Frontend build

Command:

```bash
cd frontend
npm run build
```

Result:

```text
✓ built
```

## Frontend lint

Command:

```bash
cd frontend
npm run lint
```

Result:

```text
No lint findings reported.
```
