#!/bin/bash

set -e -x

cat > Containerfile <<'EOF'
ARG BASE_IMAGE
FROM $BASE_IMAGE

# these need to be after the FROM above
ARG CONTENT_TO
ARG PACKAGES

# fix centos8 repos to use vault
RUN . /etc/os-release && \
  if [ "$VERSION_ID" = 8 ]; then \
    sed -i \
      -e 's/^mirrorlist/#mirrorlist/' \
      -e 's/^#baseurl/baseurl/' \
      -e 's|mirror\.centos\.org|vault.centos.org|g' \
      /etc/yum.repos.d/CentOS-*.repo; \
  fi

RUN dnf install -y --skip-broken --allowerasing \
  --setopt=install_weak_deps=False \
  --setopt=max_parallel_downloads=20 \
  @core python3 rsync git-core systemd dbus-broker $PACKAGES

RUN dnf clean packages

# from ATEX:
#
# on RHEL-8 (systemd 239), systemd sends out SIGTERM to all processes
# on reboot, but then waits for SIGCHLD, which does not arrive from
# non-children ... and since we use 'crun exec' in .cmd(), the exec'd
# process is never collected by PID 1 (systemd-shutdown), waiting for the
# 90sec for SIGKILL broadcast - over the 60sec of _wait_for_systemd()
# in SystemdPodmanConnection, ... so reduce the SIGKILL timer to 30sec
RUN mkdir -p /etc/systemd/system.conf.d && printf '[Manager]\nDefaultTimeoutStopSec=30s\n' > /etc/systemd/system.conf.d/container.conf

# avoid changing xattrs to remember original disk image owner
RUN if [ -d /etc/libvirt ]; then \
    echo 'remember_owner = 0' >> /etc/libvirt/qemu.conf; \
  fi

# copy built content over
COPY --from=content_from / $CONTENT_TO/

# simulate a real OS with shared-by-default mount namespaces
CMD ["sh", "-c", "mount --make-rshared / && exec /sbin/init"]
EOF

for var in "${!CONTENT_DIR_@}"; do
  stream="${var#CONTENT_DIR_}"
  base_image="quay.io/centos/centos:stream$stream"
  content_dir=${!var}

  # all packages useful to Contest tests
  contest_packages=$(
    atex fmf \
      --root "$CONTEST_DIR" \
      -c arch=$(uname -m) -c distro="centos-stream-$stream" \
      --plan /plans/stabilization \
      requires
  )

  podman image build \
    -t "cs$stream" \
    --build-arg BASE_IMAGE="$base_image" \
    --build-context "content_from=$content_dir" \
    --build-arg CONTENT_TO="$CONTENT_IN_IMAGE" \
    --build-arg PACKAGES="$contest_packages" \
    -f Containerfile .
done

# necessary for rootful --userns=auto
if ! grep -q ^containers: /etc/subuid; then
  echo "containers:2147483647:2147483648" >> /etc/subuid
fi
if ! grep -q ^containers: /etc/subgid; then
  echo "containers:2147483647:2147483648" >> /etc/subgid
fi

# needed because the default is extremely low (128) and shared across ALL
# containers (children of the init user ns) - with one systemd taking up
# ~15-20 inotify instances, we wouldn't be able to run more than ~8 namespaces
sysctl -w fs.inotify.max_user_instances=65536
