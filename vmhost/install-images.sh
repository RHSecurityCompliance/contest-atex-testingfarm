#!/bin/bash

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <name=url> [name=url]..."
  exit 1
fi

set -e -x

tmpdir=$(mktemp -d)
trap "rm -rf $tmpdir" EXIT

function run_install {
  local name=$1 url=$2 packages=$3
  local ssh_pubkey=$(cat "$VM_SSH_KEY.pub")
  atex shvirt \
    --helper-localhost \
    install \
    --name "$name" \
    --location "$url" \
    --ks-packages "$packages" \
    --ks-sshkeys "$ssh_pubkey" \
    --reserve \
    --reserve-name "img install"
}

# list contest package deps
pushd "$CONTEST_DIR"
contest_packages=$(atex fmf --plan /plans/stabilization requires)
popd

declare -A images

# verify all args before doing anything
for arg in "$@"; do
  IFS='=' read name url <<<"$arg"
  if [[ -z $name || -z $url ]]; then
    echo "error: name or url empty: $arg"
    exit 1
  fi
  images[$name]=$url
done

declare -A pids

for name in "${!images[@]}"; do
  url=${images[$name]}
  run_install "$name" "$url" "$contest_packages" &> "$tmpdir/install-$name" &
  pids[$!]=$name
done

failed=
while [[ ${#pids[@]} -gt 0 ]]; do
  if ! wait -fn -p pid; then
    failed=1
    name=${pids[$pid]}
    echo "error: '$name' failed to install"
    echo
    tail -n 500 "$tmpdir/install-$name"
    echo
  fi
  unset pids[$pid]
done

[[ $failed ]] && exit 1 || exit 0
