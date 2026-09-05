#!/usr/bin/env bash
set -euo pipefail

version="v0.9.7"
url="https://github.com/facebookincubator/FBX2glTF/releases/download/${version}/FBX2glTF-linux-x64"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
destination="${script_dir}/bin/FBX2glTF"

mkdir -p -- "${script_dir}/bin"

if [[ -x "${destination}" ]] && "${destination}" --version; then
    exit 0
fi

temporary="${destination}.download.$$"
trap 'rm -f -- "${temporary}"' EXIT
curl -fsSL "${url}" -o "${temporary}"
chmod +x "${temporary}"
"${temporary}" --version
mv -f -- "${temporary}" "${destination}"
trap - EXIT
