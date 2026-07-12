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
stage_dir="${state_parent}/.ic-env-guard-${account}.upgrade"
completed_dir="${stage_dir}.complete"
stage_state="${stage_dir}/state"
marker="${stage_dir}/marker"
lock_dir="${state_parent}/.ic-env-guard-${account}.upgrade.lock"
lock_pid="${lock_dir}/pid"
lock_owned=0
migration_active=0
cutover_started=0

validate_config() {
  "${validator}" validate "$1"
}

install_unit() {
  install -d -m 0755 "${unit_dir}"
  install -m 0644 packaging/systemd/ic-env-guard@.service \
    "${unit_dir}/ic-env-guard@.service"
  systemctl daemon-reload
}

remove_known_state_dir() {
  local directory="$1"
  if [[ ! -e "${directory}" && ! -L "${directory}" ]]; then
    return
  fi
  if [[ -L "${directory}" || ! -d "${directory}" ]]; then
    echo "refusing unsafe upgrade state path: ${directory}" >&2
    return 1
  fi
  rm -f "${directory}/token" "${directory}/state.db" \
    "${directory}/state.db-wal" "${directory}/state.db-shm" \
    "${directory}/instance-id"
  rmdir "${directory}"
}

cleanup_stage_dir() {
  local directory="$1"
  if [[ ! -e "${directory}" && ! -L "${directory}" ]]; then
    return
  fi
  if [[ -L "${directory}" || ! -d "${directory}" ]]; then
    echo "refusing unsafe upgrade staging path: ${directory}" >&2
    return 1
  fi
  remove_known_state_dir "${directory}/state"
  rm -f "${directory}/config.prepared.yaml" "${directory}/config.final.yaml" \
    "${directory}/marker.next" "${directory}/marker"
  rmdir "${directory}"
}

cleanup_stage() {
  cleanup_stage_dir "${stage_dir}"
}

marker_file_phase() {
  local marker_file="$1"
  if [[ -L "${marker_file}" || ! -f "${marker_file}" ]]; then
    return 1
  fi
  grep -Fxq 'format=ic-env-guard-legacy-upgrade-v1' "${marker_file}" || return 1
  grep -Fxq "account=${account}" "${marker_file}" || return 1
  local phase
  phase="$(sed -n 's/^phase=//p' "${marker_file}")"
  case "${phase}" in
    staging|prepared|legacy-stopped|state-published|config-published|new-started|legacy-disabled)
      printf '%s\n' "${phase}"
      ;;
    *) return 1 ;;
  esac
}

write_marker() {
  local phase="$1"
  local next="${stage_dir}/marker.next"
  {
    printf '%s\n' 'format=ic-env-guard-legacy-upgrade-v1'
    printf 'account=%s\n' "${account}"
    printf 'phase=%s\n' "${phase}"
  } > "${next}"
  chown root:root "${next}"
  chmod 0600 "${next}"
  mv "${next}" "${marker}"
  sync
}

rollback_to_legacy() {
  systemctl disable "${new_unit}" 2>/dev/null || true
  systemctl stop "${new_unit}" 2>/dev/null || true
  systemctl enable "${legacy_unit}" 2>/dev/null || true
  systemctl start "${legacy_unit}" 2>/dev/null || true
}

release_lock() {
  if [[ ${lock_owned} -eq 1 ]]; then
    rm -f "${lock_pid}"
    rmdir "${lock_dir}" 2>/dev/null || true
    lock_owned=0
  fi
}

on_exit() {
  local status=$?
  trap - EXIT
  if [[ ${status} -ne 0 && ${migration_active} -eq 1 ]]; then
    if [[ ${cutover_started} -eq 1 ]]; then
      rollback_to_legacy
    fi
    rm -f "${config_file}"
    remove_known_state_dir "${state_dir}" 2>/dev/null || true
    cleanup_stage 2>/dev/null || true
  fi
  release_lock
  exit "${status}"
}
trap on_exit EXIT

install -d -m 0755 "${config_dir}" "${state_parent}"

# mkdir is the lock primitive. A SIGKILL leaves the directory behind, so a
# later run may remove it only when its recorded owner PID is no longer alive.
if [[ -L "${lock_dir}" ]]; then
  echo "refusing unsafe upgrade lock path: ${lock_dir}" >&2
  exit 1
fi
if ! mkdir -m 0700 "${lock_dir}" 2>/dev/null; then
  if [[ ! -d "${lock_dir}" || -L "${lock_pid}" || ! -f "${lock_pid}" ]]; then
    echo "refusing unrecognized upgrade lock: ${lock_dir}" >&2
    exit 1
  fi
  owner_pid="$(cat "${lock_pid}")"
  if [[ "${owner_pid}" =~ ^[1-9][0-9]*$ ]] && kill -0 "${owner_pid}" 2>/dev/null; then
    echo "an Agent upgrade is already running for ${account}" >&2
    exit 1
  fi
  rm -f "${lock_pid}"
  rmdir "${lock_dir}" 2>/dev/null || {
    echo "cannot recover stale upgrade lock: ${lock_dir}" >&2
    exit 1
  }
  mkdir -m 0700 "${lock_dir}"
fi
lock_owned=1
printf '%s\n' "$$" > "${lock_pid}"
chmod 0600 "${lock_pid}"

# Successful cutover is committed by atomically renaming the marked staging
# directory. A kill during later cleanup therefore cannot look like an
# incomplete migration and must not roll the service back.
if [[ -e "${completed_dir}" || -L "${completed_dir}" ]]; then
  if [[ -L "${completed_dir}" || ! -d "${completed_dir}" ]] \
    || [[ "$(marker_file_phase "${completed_dir}/marker" 2>/dev/null || true)" != "legacy-disabled" ]]; then
    echo "refusing unrecognized completed upgrade staging: ${completed_dir}" >&2
    exit 1
  fi
  cleanup_stage_dir "${completed_dir}"
fi

# A valid marker proves this script owns any published target paths. Recover
# every incomplete phase to the untouched legacy layout, then retry cleanly.
if [[ -e "${stage_dir}" || -L "${stage_dir}" ]]; then
  if [[ -L "${stage_dir}" || ! -d "${stage_dir}" ]]; then
    echo "refusing unsafe upgrade staging path: ${stage_dir}" >&2
    exit 1
  fi
  if [[ ! -e "${marker}" && ! -L "${marker}" ]] \
    && marker_file_phase "${stage_dir}/marker.next" >/dev/null; then
    mv "${stage_dir}/marker.next" "${marker}"
    sync
  fi
  if phase="$(marker_file_phase "${marker}")"; then
    echo "Recovering interrupted Agent migration from phase ${phase}."
    rollback_to_legacy
    rm -f "${config_file}"
    remove_known_state_dir "${state_dir}"
    cleanup_stage
  elif [[ ! -e "${marker}" && ! -L "${marker}" \
    && ! -e "${stage_state}" && ! -L "${stage_state}" \
    && ! -e "${stage_dir}/config.prepared.yaml" \
    && ! -L "${stage_dir}/config.prepared.yaml" \
    && ! -e "${stage_dir}/config.final.yaml" \
    && ! -L "${stage_dir}/config.final.yaml" \
    && ! -L "${stage_dir}/marker.next" ]]; then
    # No durable marker means the legacy service was never stopped. Only an
    # otherwise empty stage (plus a regular partial marker.next) is removable.
    rm -f "${stage_dir}/marker.next"
    rmdir "${stage_dir}" || {
      echo "refusing unrecognized upgrade staging directory: ${stage_dir}" >&2
      exit 1
    }
  else
    echo "refusing unrecognized upgrade staging directory: ${stage_dir}" >&2
    exit 1
  fi
fi

if [[ -f "${config_file}" ]]; then
  validate_config "${config_file}"
  systemctl stop "${new_unit}" 2>/dev/null || true
  restart_new=1
  restore_current_unit() {
    status=$?
    if [[ ${status} -ne 0 && ${restart_new} -eq 1 ]]; then
      systemctl enable "${new_unit}" 2>/dev/null || true
      systemctl start "${new_unit}" 2>/dev/null || true
    fi
    return "${status}"
  }
  install_unit
  systemctl enable "${new_unit}"
  systemctl start "${new_unit}" || restore_current_unit
  restart_new=0
  echo "Agent upgraded while preserving the user config, identity, token, and state database."
  exit 0
fi

if [[ ! -f "${legacy_config}" ]]; then
  echo "no per-user or legacy Agent config found for ${account}" >&2
  exit 1
fi
if [[ -e "${state_dir}" || -L "${state_dir}" ]]; then
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

validate_config "${legacy_config}"
migration_active=1
install -d -m 0700 "${stage_dir}"
chown root:root "${stage_dir}"
write_marker staging
install -d -m 0700 "${stage_state}"
chown "${account}:${group}" "${stage_state}"
cp -p "${legacy_token}" "${stage_state}/token"
chown "${account}:${group}" "${stage_state}/token"
chmod 0600 "${stage_state}/token"
sed \
  -e "s|/var/lib/ic-env-guard/token|${stage_state}/token|g" \
  -e "s|/var/lib/ic-env-guard/state\.db|${stage_state}/state.db|g" \
  "${legacy_config}" > "${stage_dir}/config.prepared.yaml"
chown root:root "${stage_dir}/config.prepared.yaml"
chmod 0600 "${stage_dir}/config.prepared.yaml"
validate_config "${stage_dir}/config.prepared.yaml"
write_marker prepared

systemctl stop "${legacy_unit}"
cutover_started=1
write_marker legacy-stopped

for name in state.db state.db-wal state.db-shm; do
  if [[ -f "${state_parent}/${name}" ]]; then
    cp -p "${state_parent}/${name}" "${stage_state}/${name}"
    chown "${account}:${group}" "${stage_state}/${name}"
  fi
done
if [[ -f "${legacy_identity}" ]]; then
  cp -p "${legacy_identity}" "${stage_state}/instance-id"
  chown "${account}:${group}" "${stage_state}/instance-id"
  chmod 0600 "${stage_state}/instance-id"
fi
sed \
  -e "s|/var/lib/ic-env-guard/token|/var/lib/ic-env-guard/${account}/token|g" \
  -e "s|/var/lib/ic-env-guard/state\.db|/var/lib/ic-env-guard/${account}/state.db|g" \
  "${legacy_config}" > "${stage_dir}/config.final.yaml"
chown "root:${group}" "${stage_dir}/config.final.yaml"
chmod 0640 "${stage_dir}/config.final.yaml"

mv "${stage_state}" "${state_dir}"
write_marker state-published
validate_config "${stage_dir}/config.final.yaml"
mv "${stage_dir}/config.final.yaml" "${config_file}"
write_marker config-published

install_unit
systemctl enable "${new_unit}"
systemctl start "${new_unit}"
write_marker new-started
systemctl disable "${legacy_unit}"
write_marker legacy-disabled

mv "${stage_dir}" "${completed_dir}"
migration_active=0
cutover_started=0
cleanup_stage_dir "${completed_dir}"
echo "Migrated the legacy Agent to ${new_unit}; the legacy unit is disabled and its original recovery files are unchanged."
