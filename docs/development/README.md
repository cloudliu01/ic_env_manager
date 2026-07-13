# Development History

These files preserve design, planning, contract, and validation history. They
are not the source of truth for current deployment or operation. Use the root
README, `docs/guides/`, `docs/reference/`, current code, and tests for supported
behavior.

Relative links are maintained where they identify repository artifacts.
Literal paths inside historical task instructions may retain their original
spelling when changing them would misrepresent the historical record.

## Design and Implementation Plans

[Superpowers history](superpowers/) contains approved design specifications and
implementation plans produced during the Agent observability, Fleet console,
protocol-completion, and documentation work.

## Feature Specifications

[Feature specifications](specs/) contains the original Spec Kit packages:

- [Linux host Agent](specs/001-linux-host-agent/)
- [Multi-Agent control plane](specs/002-multi-agent-control-plane/)
- [Fleet overview UI](specs/003-fleet-overview-ui/)

Contracts and task lists in these packages describe their development phase;
they may include compatibility behavior or paths superseded by current guides.

## Validation Records

[Validation history](validation/) preserves platform smoke procedures,
quickstart acceptance steps, and point-in-time test results. Re-run current
commands from [Development](../guides/development.md) instead of treating an old
result as proof for the current revision.
