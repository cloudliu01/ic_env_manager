#!/usr/bin/env bash
set -euo pipefail
umask 077
set -o noclobber

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
config_temp="${config_dir}/.${account}.yaml.upgrade"
legacy_config="${config_dir}/config.yaml"
state_parent="${root}/var/lib/ic-env-guard"
state_dir="${state_parent}/${account}"
legacy_state="${state_parent}/state.db"
legacy_token="${state_parent}/token"
legacy_identity="${state_parent}/instance-id"
unit_dir="${root}/etc/systemd/system"
unit_file="${unit_dir}/ic-env-guard@.service"
unit_temp="${unit_dir}/.ic-env-guard@.service.upgrade"
unit_restore_temp="${unit_dir}/.ic-env-guard@.service.restore"
unit_backup_dir="${unit_dir}/.ic-env-guard@.service.backup"
unit_backup_build="${unit_backup_dir}.new"
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
current_upgrade=0
current_was_active=0
current_was_enabled=0

validate_config() {
  "${validator}" validate "$1"
}

path_metadata() {
  stat -c '%u %a' "$1" 2>/dev/null || stat -f '%u %Lp' "$1"
}

require_control_dir() {
  local path="$1"
  local metadata owner mode
  if [[ -L "${path}" || ! -d "${path}" ]]; then
    echo "unsafe control directory: ${path}" >&2
    return 1
  fi
  metadata="$(path_metadata "${path}")" || return 1
  read -r owner mode <<< "${metadata}"
  if [[ "${owner}" != "${EUID}" ]]; then
    echo "unsafe control directory owner: ${path}" >&2
    return 1
  fi
  if [[ "${mode}" != "700" ]]; then
    echo "unsafe control directory mode: ${path}" >&2
    return 1
  fi
}

require_control_file() {
  local path="$1"
  local metadata owner mode
  if [[ -L "${path}" || ! -f "${path}" ]]; then
    echo "unsafe control file: ${path}" >&2
    return 1
  fi
  metadata="$(path_metadata "${path}")" || return 1
  read -r owner mode <<< "${metadata}"
  if [[ "${owner}" != "${EUID}" ]]; then
    echo "unsafe control file owner: ${path}" >&2
    return 1
  fi
  if [[ "${mode}" != "600" ]]; then
    echo "unsafe control file mode: ${path}" >&2
    return 1
  fi
}

require_control_parent() {
  local path="$1"
  local metadata owner mode mode_value
  if [[ -L "${path}" || ! -d "${path}" ]]; then
    echo "unsafe control parent: ${path}" >&2
    return 1
  fi
  metadata="$(path_metadata "${path}")" || return 1
  read -r owner mode <<< "${metadata}"
  mode_value=$((8#${mode}))
  if [[ "${owner}" != "${EUID}" ]] || (( (mode_value & 8#022) != 0 )); then
    echo "unsafe control parent owner or mode: ${path}" >&2
    return 1
  fi
}

install_unit() {
  if [[ -e "${unit_dir}" || -L "${unit_dir}" ]]; then
    require_control_parent "${unit_dir}"
  fi
  install -d -m 0755 "${unit_dir}"
  require_control_parent "${unit_dir}"
  if [[ -e "${unit_temp}" || -L "${unit_temp}" ]]; then
    require_control_file "${unit_temp}"
    rm -f "${unit_temp}"
  fi
  if ! install -m 0600 packaging/systemd/ic-env-guard@.service "${unit_temp}"; then
    rm -f "${unit_temp}"
    return 1
  fi
  chown root:root "${unit_temp}"
  require_control_file "${unit_temp}"
  sync "${unit_temp}"
  mv "${unit_temp}" "${unit_file}"
  chmod 0644 "${unit_file}"
  systemctl daemon-reload
}

cleanup_backup_dir() {
  local directory="$1"
  require_control_dir "${directory}"
  for name in present absent mode active enabled; do
    if [[ -e "${directory}/${name}" || -L "${directory}/${name}" ]]; then
      require_control_file "${directory}/${name}"
      rm -f "${directory}/${name}"
    fi
  done
  rmdir "${directory}"
}

validate_backup() {
  require_control_dir "${unit_backup_dir}"
  if [[ -f "${unit_backup_dir}/present" ]]; then
    require_control_file "${unit_backup_dir}/present"
    require_control_file "${unit_backup_dir}/mode"
    [[ ! -e "${unit_backup_dir}/absent" && ! -L "${unit_backup_dir}/absent" ]]
  elif [[ -f "${unit_backup_dir}/absent" ]]; then
    require_control_file "${unit_backup_dir}/absent"
    [[ ! -e "${unit_backup_dir}/present" && ! -L "${unit_backup_dir}/present" ]]
  else
    return 1
  fi
  for name in active enabled; do
    if [[ -e "${unit_backup_dir}/${name}" || -L "${unit_backup_dir}/${name}" ]]; then
      require_control_file "${unit_backup_dir}/${name}"
    fi
  done
}

create_unit_backup() {
  local mode
  if [[ -e "${unit_backup_dir}" || -L "${unit_backup_dir}" \
    || -e "${unit_backup_build}" || -L "${unit_backup_build}" ]]; then
    echo "unsafe existing unit backup staging" >&2
    return 1
  fi
  mkdir -m 0700 "${unit_backup_build}"
  chown root:root "${unit_backup_build}"
  if [[ -f "${unit_file}" && ! -L "${unit_file}" ]]; then
    mode="$(path_metadata "${unit_file}")"
    install -m 0600 "${unit_file}" "${unit_backup_build}/present"
    chown root:root "${unit_backup_build}/present"
    printf '%s\n' "${mode##* }" > "${unit_backup_build}/mode"
    chmod 0600 "${unit_backup_build}/mode"
    sync "${unit_backup_build}/present" "${unit_backup_build}/mode"
  elif [[ ! -e "${unit_file}" && ! -L "${unit_file}" ]]; then
    : > "${unit_backup_build}/absent"
    chmod 0600 "${unit_backup_build}/absent"
    sync "${unit_backup_build}/absent"
  else
    return 1
  fi
  if [[ ${current_was_active} -eq 1 ]]; then
    : > "${unit_backup_build}/active"
    chmod 0600 "${unit_backup_build}/active"
  fi
  if [[ ${current_was_enabled} -eq 1 ]]; then
    : > "${unit_backup_build}/enabled"
    chmod 0600 "${unit_backup_build}/enabled"
  fi
  mv "${unit_backup_build}" "${unit_backup_dir}"
}

restore_unit_backup() {
  local mode
  validate_backup
  systemctl stop "${new_unit}" 2>/dev/null || true
  if [[ -f "${unit_backup_dir}/present" ]]; then
    mode="$(cat "${unit_backup_dir}/mode")"
    install -m 0600 "${unit_backup_dir}/present" "${unit_restore_temp}"
    chown root:root "${unit_restore_temp}"
    require_control_file "${unit_restore_temp}"
    sync "${unit_restore_temp}"
    mv "${unit_restore_temp}" "${unit_file}"
    chmod "${mode}" "${unit_file}"
  else
    rm -f "${unit_file}"
  fi
  systemctl daemon-reload 2>/dev/null || true
  current_was_active=0
  current_was_enabled=0
  [[ -f "${unit_backup_dir}/active" ]] && current_was_active=1
  [[ -f "${unit_backup_dir}/enabled" ]] && current_was_enabled=1
  restore_current_state
  cleanup_backup_dir "${unit_backup_dir}"
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
  require_control_dir "${directory}"
  remove_known_state_dir "${directory}/state"
  for control_file in "${directory}/marker.next" "${directory}/marker" \
    "${directory}/config.prepared.yaml" "${directory}/config.final.yaml"; do
    if [[ -e "${control_file}" || -L "${control_file}" ]]; then
      require_control_file "${control_file}"
    fi
  done
  rm -f "${directory}/config.prepared.yaml" "${directory}/config.final.yaml" \
    "${directory}/marker.next" "${directory}/marker"
  rmdir "${directory}"
}

cleanup_stage() {
  cleanup_stage_dir "${stage_dir}"
}

marker_file_phase() {
  local marker_file="$1"
  require_control_file "${marker_file}" >/dev/null || return 1
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

remove_config_temp() {
  if [[ ! -e "${config_temp}" && ! -L "${config_temp}" ]]; then
    return
  fi
  require_control_file "${config_temp}"
  rm -f "${config_temp}"
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

restore_current_state() {
  if [[ ${current_was_enabled} -eq 1 ]]; then
    systemctl enable "${new_unit}" 2>/dev/null || true
  else
    systemctl disable "${new_unit}" 2>/dev/null || true
  fi
  if [[ ${current_was_active} -eq 1 ]]; then
    systemctl start "${new_unit}" 2>/dev/null || true
  else
    systemctl stop "${new_unit}" 2>/dev/null || true
  fi
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
    remove_config_temp 2>/dev/null || true
    remove_known_state_dir "${state_dir}" 2>/dev/null || true
    cleanup_stage 2>/dev/null || true
  fi
  if [[ ${status} -ne 0 && ${current_upgrade} -eq 1 ]]; then
    if [[ -e "${unit_backup_dir}" || -L "${unit_backup_dir}" ]]; then
      restore_unit_backup 2>/dev/null || true
    else
      restore_current_state
    fi
  fi
  release_lock
  exit "${status}"
}
trap on_exit EXIT

if [[ -e "${config_dir}" || -L "${config_dir}" ]]; then
  require_control_parent "${config_dir}"
fi
if [[ -e "${state_parent}" || -L "${state_parent}" ]]; then
  require_control_parent "${state_parent}"
fi
install -d -m 0755 "${config_dir}" "${state_parent}"
require_control_parent "${config_dir}"
require_control_parent "${state_parent}"

# mkdir is the lock primitive. A SIGKILL leaves the directory behind, so a
# later run may remove it only when its recorded owner PID is no longer alive.
if [[ -L "${lock_dir}" ]]; then
  echo "refusing unsafe upgrade lock path: ${lock_dir}" >&2
  exit 1
fi
if ! mkdir -m 0700 "${lock_dir}" 2>/dev/null; then
  require_control_dir "${lock_dir}"
  if [[ ! -d "${lock_dir}" || -L "${lock_pid}" || ! -f "${lock_pid}" ]]; then
    echo "refusing unrecognized upgrade lock: ${lock_dir}" >&2
    exit 1
  fi
  require_control_file "${lock_pid}"
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
require_control_dir "${lock_dir}"
require_control_file "${lock_pid}"

if [[ -e "${unit_dir}" || -L "${unit_dir}" ]]; then
  require_control_parent "${unit_dir}"
  for leftover in "${unit_temp}" "${unit_restore_temp}"; do
    if [[ -e "${leftover}" || -L "${leftover}" ]]; then
      require_control_file "${leftover}"
      rm -f "${leftover}"
    fi
  done
  if [[ -e "${unit_backup_build}" || -L "${unit_backup_build}" ]]; then
    cleanup_backup_dir "${unit_backup_build}"
  fi
  if [[ -e "${unit_backup_dir}" || -L "${unit_backup_dir}" ]]; then
    restore_unit_backup
  fi
fi

# Successful cutover is committed by atomically renaming the marked staging
# directory. A kill during later cleanup therefore cannot look like an
# incomplete migration and must not roll the service back.
if [[ -e "${completed_dir}" || -L "${completed_dir}" ]]; then
  if ! require_control_dir "${completed_dir}" \
    || [[ "$(marker_file_phase "${completed_dir}/marker" 2>/dev/null || true)" != "legacy-disabled" ]]; then
    echo "refusing unrecognized completed upgrade staging: ${completed_dir}" >&2
    exit 1
  fi
  cleanup_stage_dir "${completed_dir}"
fi

# A valid marker proves this script owns any published target paths. Recover
# every incomplete phase to the untouched legacy layout, then retry cleanly.
if [[ -e "${stage_dir}" || -L "${stage_dir}" ]]; then
  require_control_dir "${stage_dir}"
  if [[ ! -e "${marker}" && ! -L "${marker}" ]] \
    && marker_file_phase "${stage_dir}/marker.next" >/dev/null; then
    mv "${stage_dir}/marker.next" "${marker}"
    sync
  fi
  if phase="$(marker_file_phase "${marker}")"; then
    echo "Recovering interrupted Agent migration from phase ${phase}."
    rollback_to_legacy
    rm -f "${config_file}"
    remove_config_temp
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
    if [[ -e "${stage_dir}/marker.next" ]]; then
      require_control_file "${stage_dir}/marker.next"
      rm -f "${stage_dir}/marker.next"
    fi
    rmdir "${stage_dir}" || {
      echo "refusing unrecognized upgrade staging directory: ${stage_dir}" >&2
      exit 1
    }
  else
    echo "refusing unrecognized upgrade staging directory: ${stage_dir}" >&2
    exit 1
  fi
fi

if [[ -e "${config_temp}" || -L "${config_temp}" ]]; then
  echo "refusing unrecognized config staging file: ${config_temp}" >&2
  exit 1
fi

if [[ -f "${config_file}" ]]; then
  validate_config "${config_file}"
  if systemctl is-active "${new_unit}" >/dev/null 2>&1; then
    current_was_active=1
  fi
  if systemctl is-enabled "${new_unit}" >/dev/null 2>&1; then
    current_was_enabled=1
  fi
  current_upgrade=1
  if [[ -e "${unit_dir}" || -L "${unit_dir}" ]]; then
    require_control_parent "${unit_dir}"
  fi
  install -d -m 0755 "${unit_dir}"
  require_control_parent "${unit_dir}"
  create_unit_backup
  install_unit
  if [[ ${current_was_enabled} -eq 1 ]]; then
    systemctl enable "${new_unit}"
  fi
  if [[ ${current_was_active} -eq 1 ]]; then
    systemctl stop "${new_unit}"
    systemctl start "${new_unit}"
  fi
  cleanup_backup_dir "${unit_backup_dir}"
  current_upgrade=0
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
  "${legacy_config}" > "${config_temp}"
chown root:root "${config_temp}"
chmod 0600 "${config_temp}"
sync "${config_temp}"

mv "${stage_state}" "${state_dir}"
write_marker state-published
validate_config "${config_temp}"
mv "${config_temp}" "${config_file}"
chown "root:${group}" "${config_file}"
chmod 0640 "${config_file}"
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
