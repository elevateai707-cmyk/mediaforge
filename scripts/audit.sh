#!/usr/bin/env bash
# Read-only dependency and SBOM audit.  This never installs or upgrades packages.
set -u

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${AUDIT_OUTPUT_DIR:-$repo_root/work/audit}"
mkdir -p "$output_dir"

run_pip_audit() {
  local requirements="$repo_root/backend/requirements.txt"
  local backend_python="$repo_root/backend/.venv/bin/python"
  if [ -x "$backend_python" ] && "$backend_python" -m pip_audit --help >/dev/null 2>&1; then
    "$backend_python" -m pip_audit -r "$requirements" -f json >"$output_dir/pip-audit.json" || return $?
  elif command -v pip-audit >/dev/null 2>&1; then
    pip-audit -r "$requirements" -f json >"$output_dir/pip-audit.json" || return $?
  elif python3 -m pip_audit --help >/dev/null 2>&1; then
    python3 -m pip_audit -r "$requirements" -f json >"$output_dir/pip-audit.json" || return $?
  else
    printf '%s\n' 'pip-audit is not installed in backend/.venv or on PATH; skipped.' >"$output_dir/pip-audit.txt"
    return 0
  fi
}

run_npm_audit() {
  if [ -f "$repo_root/frontend/package.json" ] && command -v npm >/dev/null 2>&1; then
    (cd "$repo_root/frontend" && npm audit --json) >"$output_dir/npm-audit.json" || return $?
  fi
  return 0
}

run_syft() {
  if command -v syft >/dev/null 2>&1; then
    syft "dir:$repo_root" -o cyclonedx-json >"$output_dir/sbom.cdx.json"
  else
    printf '%s\n' 'syft is not installed; SBOM skipped.' >"$output_dir/syft.txt"
  fi
}

status=0
run_pip_audit || status=$?
run_npm_audit || status=$?
run_syft || status=$?
exit "$status"
