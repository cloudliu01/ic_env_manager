#!/usr/bin/env bash
set -euo pipefail

account="${1:-}"
if [[ -z "${account}" ]]; then
  echo "usage: $0 <existing-linux-user>" >&2
  exit 2
fi
if ! id "${account}" >/dev/null 2>&1; then
  echo "existing Linux user not found: ${account}" >&2
  exit 2
fi
if [[ "$(id -u "${account}")" -eq 0 ]]; then
  echo "the Agent must not run as root; select an existing non-root user" >&2
  exit 2
fi

group="$(id -gn "${account}")"
root="${IC_ENV_GUARD_ROOT:-}"
config_dir="${root}/etc/ic-env-guard"
config_file="${config_dir}/${account}.yaml"
legacy_config="${config_dir}/config.yaml"
state_parent="${root}/var/lib/ic-env-guard"
state_dir="${state_parent}/${account}"
legacy_state="${state_parent}/state.db"
legacy_token="${state_parent}/token"
legacy_identity="${state_parent}/instance-id"
unit_dir="${root}/etc/systemd/system"
new_unit="ic-env-guard@${account}.service"
legacy_unit="ic-env-guard.service"
validator="${IC_ENV_GUARD_CONFIG_VALIDATE:-ic-env-guard-config}"

validate_config() {
  "${validator}" validate "$1"
}

install_unit() {
  install -d -m 0755 "${unit_dir}"
  install -m 0644 packaging/systemd/ic-env-guard@.service \
    "${unit_dir}/ic-env-guard@.service"
  systemctl daemon-reload
}

if [[ -f "${config_file}" ]]; then
  validate_config "${config_file}"
  systemctl stop "${new_unit}" 2>/dev/null || true
  restart_new=1
  restore_current_unit() {
    status=$?
    trap - EXIT
    if [[ ${status} -ne 0 && ${restart_new} -eq 1 ]]; then
      systemctl start "${new_unit}" 2>/dev/null || true
    fi
    exit "${status}"
  }
  trap restore_current_unit EXIT
  install_unit
  systemctl start "${new_unit}"
  restart_new=0
  trap - EXIT
  echo "Agent upgraded while preserving the user config, identity, token, and state database."
  exit 0
fi

if [[ ! -f "${legacy_config}" ]]; then
  echo "no per-user or legacy Agent config found for ${account}" >&2
  exit 1
fi
if [[ -e "${state_dir}" ]]; then
  echo "refusing legacy migration because ${state_dir} already exists" >&2
  exit 1
fi
if [[ ! -f "${legacy_token}" || ! -f "${legacy_state}" ]]; then
  echo "legacy migration requires ${legacy_token} and ${legacy_state}" >&2
  exit 1
fi
if ! grep -Eq '^[[:space:]]*token_file:[[:space:]]*/var/lib/ic-env-guard/token[[:space:]]*$' "${legacy_config}" \
  || ! grep -Eq '^[[:space:]]*state_database:[[:space:]]*/var/lib/ic-env-guard/state\.db[[:space:]]*$' "${legacy_config}"; then
  echo "legacy config uses customized token/state paths; migrate it manually before upgrade" >&2
  exit 1
fi

token_tmp="${state_dir}/.token.upgrade.$$"
config_tmp="${config_file}.upgrade.$$"
legacy_stopped=0
new_start_attempted=0
migration_complete=0
recover_legacy() {
  status=$?
  trap - EXIT
  if [[ ${status} -ne 0 ]]; then
    if [[ ${new_start_attempted} -eq 1 ]]; then
      systemctl stop "${new_unit}" 2>/dev/null || true
    fi
    if [[ ${legacy_stopped} -eq 1 ]]; then
      systemctl enable "${legacy_unit}" 2>/dev/null || true
      systemctl start "${legacy_unit}" 2>/dev/null || true
    fi
    if [[ ${migration_complete} -eq 0 ]]; then
      rm -f "${token_tmp}" "${config_tmp}" "${config_file}"
      rm -f "${state_dir}/token" "${state_dir}/state.db" \
        "${state_dir}/state.db-wal" "${state_dir}/state.db-shm" \
        "${state_dir}/instance-id"
      rmdir "${state_dir}" 2>/dev/null || true
    fi
  fi
  exit "${status}"
}
trap recover_legacy EXIT

# Validate the source first, then stage the target token and config while the
# legacy unit is still running. The old service is not stopped until both
# source and staged target configurations have passed validation.
validate_config "${legacy_config}"
install -d -m 0755 "${config_dir}" "${state_parent}"
install -d -m 0700 "${state_dir}"
chown "${account}:${group}" "${state_dir}"
cp -p "${legacy_token}" "${token_tmp}"
chown "${account}:${group}" "${token_tmp}"
chmod 0600 "${token_tmp}"
mv "${token_tmp}" "${state_dir}/token"
sed \
  -e "s|/var/lib/ic-env-guard/token|/var/lib/ic-env-guard/${account}/token|g" \
  -e "s|/var/lib/ic-env-guard/state\.db|/var/lib/ic-env-guard/${account}/state.db|g" \
  "${legacy_config}" > "${config_tmp}"
chown "root:${group}" "${config_tmp}"
chmod 0640 "${config_tmp}"
validate_config "${config_tmp}"

systemctl stop "${legacy_unit}"
legacy_stopped=1

for name in state.db state.db-wal state.db-shm; do
  if [[ -f "${state_parent}/${name}" ]]; then
    artifact_tmp="${state_dir}/.${name}.upgrade.$$"
    cp -p "${state_parent}/${name}" "${artifact_tmp}"
    chown "${account}:${group}" "${artifact_tmp}"
    mv "${artifact_tmp}" "${state_dir}/${name}"
  fi
done
if [[ -f "${legacy_identity}" ]]; then
  identity_tmp="${state_dir}/.instance-id.upgrade.$$"
  cp -p "${legacy_identity}" "${identity_tmp}"
  chown "${account}:${group}" "${identity_tmp}"
  chmod 0600 "${identity_tmp}"
  mv "${identity_tmp}" "${state_dir}/instance-id"
fi

mv "${config_tmp}" "${config_file}"
validate_config "${config_file}"
install_unit
new_start_attempted=1
systemctl start "${new_unit}"
systemctl disable "${legacy_unit}"
migration_complete=1
trap - EXIT

echo "Migrated the legacy Agent to ${new_unit}; the legacy unit is disabled and its original recovery files are unchanged."
