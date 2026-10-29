#!/usr/bin/env bash
set -euo pipefail
umask 077

deploy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    echo "Usage: $0 [path/to/settings.yml]"
    echo "Install on a fresh Ubuntu 24.04 amd64 VM; requires sudo and Internet access."
    exit 0
fi
if [[ $# -gt 1 ]]; then
    echo "Expected at most one settings file. See --help." >&2
    exit 1
fi
if [[ ! -f /etc/os-release ]]; then
    echo "This installer requires Ubuntu 24.04 amd64." >&2
    exit 1
fi
# The OS identification file is supplied by the operating system.
source /etc/os-release
if [[ "$ID" != "ubuntu" || "$VERSION_ID" != "24.04" || "$(uname -m)" != "x86_64" ]]; then
    echo "This installer requires Ubuntu 24.04 amd64." >&2
    exit 1
fi

settings_file="$(realpath -- "${1:-$deploy_dir/settings.yml}")"
if [[ ! -f "$settings_file" ]]; then
    echo "Copy deploy/settings.example.yml to deploy/settings.yml and fill it in first." >&2
    exit 1
fi
if [[ $(( 8#$(stat -c '%a' -- "$settings_file") & 077 )) -ne 0 ]]; then
    echo "The settings file contains passwords. Run: chmod 600 '$settings_file'" >&2
    exit 1
fi
if [[ "$EUID" -ne 0 ]]; then
    exec sudo -- "$deploy_dir/install.sh" "$settings_file"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3-venv ca-certificates
python3 -m venv /opt/ida-deploy/venv
/opt/ida-deploy/venv/bin/python -m pip install --requirement "$deploy_dir/requirements.txt"
export ANSIBLE_CONFIG="$deploy_dir/ansible.cfg"
export ANSIBLE_COLLECTIONS_PATH=/opt/ida-deploy/collections
/opt/ida-deploy/venv/bin/ansible-galaxy collection install \
    --requirements-file "$deploy_dir/requirements.yml" --collections-path "$ANSIBLE_COLLECTIONS_PATH"

# JSON preserves spaces in a settings-file path without passing passwords as arguments.
settings_argument="$(/opt/ida-deploy/venv/bin/python -c \
    'import json, sys; print(json.dumps({"ida_settings_file": sys.argv[1]}))' "$settings_file")"
exec /opt/ida-deploy/venv/bin/ansible-playbook \
    --inventory localhost, --connection local \
    "$deploy_dir/ansible/install.yml" --extra-vars "$settings_argument"
