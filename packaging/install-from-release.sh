#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install-from-release.sh --tarball <path_or_url> [--install-dir <dir>]

Install a release tarball locally. Defaults are local-first and avoid central
network lookups when possible.

Options:
  --tarball <path|url>           Release file path or URL
  --install-dir <dir>             Destination root (default: /usr/local/fleet-node-observability)
  --overwrite                     Replace existing install directory
  --help
USAGE
}

INSTALL_DIR="/usr/local/fleet-node-observability"
TARBALL=""
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
  rm -rf "${INSTALL_DIR:?}"/*
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
