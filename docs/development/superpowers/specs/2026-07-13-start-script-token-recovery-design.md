# Start Script Development Token Recovery Design

**Date:** 2026-07-13  
**Status:** Approved design, pending implementation review

## Context

`./start.sh all` creates separate bearer-token files for the local Agent and
Manager. The script currently creates a token only when its target path does
not exist. If token generation is interrupted after shell redirection creates
the file but before Python writes the generated value, a zero-byte file
remains. Later runs treat that file as an existing token.

Configuration validation checks the configuration shape and token-file
permissions, so it reports `configuration valid`. Manager composition then
calls `load_bearer_token`, which strips the file contents and rejects the empty
value. This produces a late startup failure:

```text
ValueError: bearer token file is empty
```

The failure is not caused by Agent and Manager sharing credentials. In the
`all` workflow they use distinct `agent.token` and `control-plane.token` paths.

## Goals

- Make all `start.sh` development modes recover automatically from a missing,
  zero-byte, or whitespace-only generated token.
- Preserve every existing non-empty token so normal restarts do not rotate
  credentials or invalidate a browser session.
- Generate replacement tokens without exposing a partially written target
  file.
- Keep generated token files owner-only (`0600`).
- Cover the behavior with an automated test that invokes the real development
  script.

## Non-goals

- Do not change production runtime token loading or validation semantics.
- Do not make the backend runtime generate or rotate credentials.
- Do not add user-configurable token rotation to `start.sh`.
- Do not alter Agent/Manager ports, configuration files, or process lifecycle.
- Do not repair non-empty tokens with unsafe permissions; existing validation
  remains responsible for rejecting those files.

## Considered Approaches

### 1. Atomic automatic recovery in `start.sh` — selected

Treat missing and blank development tokens as incomplete generated state.
Generate a replacement in a private temporary file and atomically move it to
the configured token path. Preserve non-blank files unchanged.

This retains the one-command development experience while keeping credential
generation at the layer that already owns development credentials.

### 2. Fail early with manual recovery instructions

The script could reject a blank file before configuration validation and tell
the operator to delete it. This would improve the error message but leave
`./start.sh all` unable to recover from its own interrupted write.

### 3. Generate a token in the backend runtime

The backend could repair an empty token while composing the application. This
would blur the production authentication boundary and allow a runtime startup
to mutate credentials, so it is intentionally rejected.

## Design

### Token helper

`start.sh` will define one `ensure_dev_token <path>` helper. The helper owns
only development-token creation and follows these rules:

1. If the target is a regular file containing at least one non-whitespace
   character, return without changing its contents or metadata.
2. Otherwise, create a temporary file next to the target so replacement stays
   on the same filesystem.
3. Generate one `secrets.token_urlsafe(32)` value into the temporary file.
4. Set the temporary file mode to `0600`.
5. Atomically replace the target with the temporary file.
6. Remove the temporary file if generation or permission setup fails.

The existing private development directory (`0700`) remains the parent for
default token paths. A configured token path outside that directory continues
to be the caller's responsibility, as it is today.

### Call sites

`ensure_dev_config` will call the helper for the selected runtime's
`TOKEN_FILE`. In control-plane mode it will also call the helper for
`AGENT_TOKEN_FILE`. This replaces the two duplicated missing-file generation
blocks.

The generated YAML paths and the `validate_config` sequence remain unchanged.
After recovery, validation and runtime startup consume the same non-empty
token.

### Existing state

- Missing file: create a token.
- Zero-byte file: replace it with a generated token.
- Whitespace-only file: replace it with a generated token because the runtime
  treats it as empty after stripping.
- Non-empty file: preserve it exactly.
- Non-empty file with unsafe permissions: preserve it, then let existing
  configuration validation fail closed.

## Security and Failure Handling

Writing to a sibling temporary file and replacing the target avoids exposing
the zero-byte window created by direct shell redirection. `umask 077` and an
explicit `chmod 0600` protect the temporary file before it becomes visible at
the final path.

If token generation fails, `set -e` stops startup. Cleanup removes the
temporary file, while an existing target remains unchanged until the atomic
replacement succeeds. No token value is written to console output or logs.

This recovery is limited to `start.sh`, which is explicitly the local
development launcher. Production systemd and CLI flows continue to reject an
empty token rather than inventing credentials.

## Test Strategy

Add focused integration coverage around the real script:

1. Create an isolated development directory with an empty
   `control-plane.token`.
2. Run `./start.sh config control-plane` with dependency installation skipped.
3. Assert the command succeeds, the token contains a non-whitespace value, and
   its permission mode is `0600`.
4. Run the command again and assert the valid token value is unchanged.
5. Add equivalent whitespace-only coverage if it can share the same focused
   test without obscuring failure diagnosis.

Then run the affected integration test, the full backend test suite, Ruff,
`./start.sh config agent`, `./start.sh config control-plane`, and a bounded
`./start.sh all` smoke test that confirms both `/healthz` endpoints become
ready before terminating the process group.

## Acceptance Criteria

- The reported zero-byte `control-plane.token` state is repaired without
  manual deletion.
- Agent and Manager start with distinct, non-empty token files.
- Re-running the script preserves existing valid token values.
- A failed generation attempt cannot replace a valid token with an empty file.
- Production authentication behavior is unchanged.
- Targeted and full regression checks pass.
