#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install-from-release.sh --tarball <path_or_url> [--install-dir <dir>]

Install a release tarball locally. Defaults are local-first and avoid central
network lookups when possible.

Options:
  --tarball <path|url>           Release file path or URL
  --sha256 <hex|path>             Expected tarball SHA256 or checksum file path
  --install-dir <dir>             Destination root (default: /usr/local/fleet-node-observability)
  --overwrite                     Replace existing install directory
  --help
USAGE
}

INSTALL_DIR="/usr/local/fleet-node-observability"
TARBALL=""
SHA256_SOURCE=""
OVERWRITE=0

while (( "$#" )); do
  case "$1" in
    --tarball)
      if [[ "$#" -lt 2 ]]; then
        echo "--tarball requires a value" >&2
        usage
        exit 1
      fi
      shift
      TARBALL="${1:-}"
      ;;
    --install-dir)
      if [[ "$#" -lt 2 ]]; then
        echo "--install-dir requires a value" >&2
        usage
        exit 1
      fi
      shift
      INSTALL_DIR="${1:-}"
      ;;
    --sha256)
      if [[ "$#" -lt 2 ]]; then
        echo "--sha256 requires a value" >&2
        usage
        exit 1
      fi
      shift
      SHA256_SOURCE="${1:-}"
      ;;
    --overwrite)
      OVERWRITE=1
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
  shift
done

if [[ -z "${TARBALL}" ]]; then
  echo "--tarball is required." >&2
  usage
  exit 1
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

canonical_install_dir() {
  local target="$1"
  local parent
  local base

  if [[ -z "${target}" || "${target}" != /* ]]; then
    echo "Install directory must be an absolute path." >&2
    exit 1
  fi
  case "${target}" in
    /|/usr|/usr/|/usr/local|/usr/local/|/Library|/Library/|/opt|/opt/|/var|/var/|/tmp|/tmp/)
      echo "Refusing dangerous install directory: ${target}" >&2
      exit 1
      ;;
    *"/.."*|*"../"*|*"/."*|*"./"*)
      echo "Install directory must not contain . or .. path segments: ${target}" >&2
      exit 1
      ;;
  esac

  base="$(basename "${target}")"
  if [[ "${base}" != "fleet-node-observability" ]]; then
    echo "Install directory basename must be fleet-node-observability: ${target}" >&2
    exit 1
  fi

  parent="$(dirname "${target}")"
  mkdir -p "${parent}"
  (cd "${parent}" && printf '%s/%s\n' "$(pwd -P)" "${base}")
}

validate_tarball_members() {
  local archive="$1"
  local entry

  while IFS= read -r entry; do
    if [[ -z "${entry}" || "${entry}" == /* || "${entry}" == *"/../"* || "${entry}" == ../* || "${entry}" == *"/.." ]]; then
      echo "Invalid tarball member path: ${entry}" >&2
      exit 1
    fi
  done < <(tar -tzf "${archive}")

  if tar -tvzf "${archive}" | awk '$1 ~ /^[hlbcps]/ { found = 1 } END { exit found ? 0 : 1 }'; then
    echo "Invalid tarball format (links and special files are not allowed)." >&2
    exit 1
  fi
}

read_expected_sha256() {
  local source="$1"
  local expected

  if [[ -f "${source}" ]]; then
    expected="$(awk 'NF { print $1; exit }' "${source}")"
  else
    expected="${source}"
  fi

  expected="$(printf '%s' "${expected}" | tr '[:upper:]' '[:lower:]')"
  if ! [[ "${expected}" =~ ^[a-f0-9]{64}$ ]]; then
    echo "SHA256 value must be a 64-character hex digest or checksum file: ${source}" >&2
    exit 1
  fi
  printf '%s\n' "${expected}"
}

verify_sha256() {
  local archive="$1"
  local checksum_source="$2"
  local expected
  local actual

  expected="$(read_expected_sha256 "${checksum_source}")"
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "${archive}" | awk '{ print $1 }')"
  else
    actual="$(shasum -a 256 "${archive}" | awk '{ print $1 }')"
  fi
  actual="$(printf '%s' "${actual}" | tr '[:upper:]' '[:lower:]')"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "SHA256 mismatch for release tarball." >&2
    echo "Expected: ${expected}" >&2
    echo "Actual:   ${actual}" >&2
    exit 1
  fi
}

default_local_sha256_file() {
  local source="$1"
  local candidate

  candidate="${source%.tar.gz}.sha256"
  if [[ -f "${candidate}" ]]; then
    printf '%s\n' "${candidate}"
    return 0
  fi
  candidate="${source}.sha256"
  if [[ -f "${candidate}" ]]; then
    printf '%s\n' "${candidate}"
    return 0
  fi
  return 1
}

remote_sha256_url() {
  local source="$1"
  local candidate

  candidate="${source%.tar.gz}.sha256"
  if [[ "${candidate}" == "${source}" ]]; then
    candidate="${source}.sha256"
  fi
  printf '%s\n' "${candidate}"
}

download_if_url() {
  local source="$1"
  local dest="$2"

  if [[ "${source}" == http://* || "${source}" == https://* ]]; then
    if ! command -v curl >/dev/null 2>&1; then
      echo "curl is required to fetch remote tarballs." >&2
      exit 1
    fi
    curl -fsSL "${source}" -o "${dest}"
    return
  fi

  if [[ -f "${source}" ]]; then
    cp "${source}" "${dest}"
    return
  fi

  echo "Cannot resolve tarball source: ${source}" >&2
  exit 1
}

if [[ "${TARBALL}" == http://* || "${TARBALL}" == https://* || -f "${TARBALL}" ]]; then
  download_if_url "${TARBALL}" "${tmpdir}/release.tar.gz"
else
  echo "Tarball must be a local path or URL." >&2
  echo "Example: --tarball ./dist/fleet-node-observability-0.1.0.tar.gz" >&2
  echo "Example: --tarball https://github.com/example/fleet-node-observability/releases/download/v0.1.0/fleet-node-observability-0.1.0.tar.gz" >&2
  exit 1
fi

if [[ -z "${SHA256_SOURCE}" ]]; then
  if [[ "${TARBALL}" == http://* || "${TARBALL}" == https://* ]]; then
    SHA256_SOURCE="${tmpdir}/release.sha256"
    if ! curl -fsSL "$(remote_sha256_url "${TARBALL}")" -o "${SHA256_SOURCE}"; then
      echo "Remote tarball installs require SHA256 verification. Pass --sha256 or publish a matching .sha256 sidecar." >&2
      exit 1
    fi
  elif default_local_sha256_file "${TARBALL}" >/dev/null 2>&1; then
    SHA256_SOURCE="$(default_local_sha256_file "${TARBALL}")"
  fi
fi

if [[ -n "${SHA256_SOURCE}" ]]; then
  verify_sha256 "${tmpdir}/release.tar.gz" "${SHA256_SOURCE}"
fi

INSTALL_DIR="$(canonical_install_dir "${INSTALL_DIR}")"
validate_tarball_members "${tmpdir}/release.tar.gz"

mkdir -p "${tmpdir}/extract"
tar -xzf "${tmpdir}/release.tar.gz" -C "${tmpdir}/extract"
extract_root_count="$(find "${tmpdir}/extract" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d '[:space:]')"
extract_file_count="$(find "${tmpdir}/extract" -mindepth 1 -maxdepth 1 -type f | wc -l | tr -d '[:space:]')"

if [[ "${extract_root_count}" -ne 1 || "${extract_file_count}" -ne 0 ]]; then
  echo "Invalid tarball format (expected exactly one top-level directory)." >&2
  exit 1
fi
extract_root="$(find "${tmpdir}/extract" -mindepth 1 -maxdepth 1 -type d | sort | head -n 1)"

for required_path in README.md VERSION bin src; do
  if [[ ! -e "${extract_root}/${required_path}" ]]; then
    echo "Invalid tarball format (missing ${required_path})." >&2
    exit 1
  fi
done

if [[ -d "${INSTALL_DIR}" && "${OVERWRITE}" -eq 0 ]]; then
  echo "Install directory already exists: ${INSTALL_DIR}" >&2
  echo "Use --overwrite to replace it." >&2
  exit 1
fi

mkdir -p "${INSTALL_DIR}"
if [[ "${OVERWRITE}" -eq 1 ]]; then
  find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
fi

cp -R "${extract_root}/." "${INSTALL_DIR}/"
if [[ -f "${INSTALL_DIR}/packaging/build-release.sh" ]]; then
  chmod +x "${INSTALL_DIR}/packaging/build-release.sh"
fi
if [[ -f "${INSTALL_DIR}/packaging/install-from-release.sh" ]]; then
  chmod +x "${INSTALL_DIR}/packaging/install-from-release.sh"
fi

cat <<EOF
Installed:
  Path: ${INSTALL_DIR}

Next steps:
  1) cd ${INSTALL_DIR}
  2) Review docs/ install* and examples/* configs before running commands
EOF
