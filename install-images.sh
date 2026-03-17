#!/bin/bash

set -e -x

tmpdir=$(mktemp -d)
trap "rm -rf '$tmpdir'" EXIT

function run_install {
  local name=$1 url=$2 packages=$3
  local ssh_pubkey=$(cat "$VM_SSH_KEY.pub")

  # fix repositories on old CentOS Streams that live on vault now
  local fix_repo=
  if [[ $url == *vault.centos.org* ]]; then
    lines=(
      "%post"
      "sed -i \\"
      "  -e 's/^mirrorlist/#mirrorlist/' \\"
      "  -e 's/^#baseurl/baseurl/' \\"
      "  -e 's/mirror\\.centos\\.org/vault.centos.org/' \\"
      "  /etc/yum.repos.d/CentOS-*.repo"
      "%end"
    )
    printf -v fix_repo '%s\n' "${lines[@]}"
  fi

  atex shvirt \
    --helper-localhost \
    install \
    --name "$name" \
    --location "$url" \
    --ks-packages "$packages" \
    --ks-sshkeys "$ssh_pubkey" \
    --ks-append "$fix_repo" \
    --reserve \
    --reserve-name "img install"
}

# list contest package deps
pushd "$CONTEST_DIR"
contest_packages=$(atex fmf --plan /plans/stabilization requires)
popd

declare -A pids

for var in "${!INSTALL_URL_@}"; do
  stream="${var#INSTALL_URL_}"
  name="cs$stream"
  url=${!var}
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

# embed built content into the images
for var in "${!CONTENT_DIR_@}"; do
  stream="${var#CONTENT_DIR_}"
  image="/var/lib/libvirt/images/cs$stream"
  dir=${!var}
  virt-copy-in -a "$image" "$dir" "$CONTENT_ON_VM"
done
