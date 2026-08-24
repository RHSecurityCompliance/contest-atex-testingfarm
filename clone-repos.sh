#!/bin/bash

set -e -x

# if set, treat Packit variables as Contest and use a static CaC/content,
if [[ $TEST_CONTEST_INSTEAD == 1 ]]; then
  # clone CaC/content, that's easy
  # - use a temporary dir, we'll copy it to per-stream dirs later
  git clone --depth=1 https://github.com/ComplianceAsCode/content.git temp_content
  if [[ $CONTENT_PR ]]; then
    echo "Checking out CaC/content PR:$CONTENT_PR"
    pushd temp_content
    git fetch origin "refs/pull/$CONTENT_PR/head"
    git reset --hard FETCH_HEAD
    popd
  fi
  # clone the Contest PR's code, double-check that the Packit-provided SHA exists
  git clone -b "$PACKIT_SOURCE_BRANCH" --depth=1 "$PACKIT_SOURCE_URL" "$CONTEST_DIR"
  if [[ $PACKIT_SOURCE_SHA ]]; then
    pushd "$CONTEST_DIR"
    git checkout -f "$PACKIT_SOURCE_SHA"
    popd
  fi

# otherwise treat Packit as CaC/content and use static Contest
else
  # clone Contest, that's easy
  git clone --depth=1 https://github.com/RHSecurityCompliance/contest.git "$CONTEST_DIR"
  if [[ $CONTEST_PR ]]; then
    echo "Checking out Contest PR:$CONTEST_PR"
    pushd "$CONTEST_DIR"
    git fetch origin "refs/pull/$CONTEST_PR/head"
    git reset --hard FETCH_HEAD
    popd
  fi
  # clone the CaC/content PR's code, double-check that the Packit-provided SHA exists
  # - use a temporary dir, we'll copy it to per-stream dirs later
  git clone -b "$PACKIT_SOURCE_BRANCH" --depth=1 "$PACKIT_SOURCE_URL" temp_content
  if [[ $PACKIT_SOURCE_SHA ]]; then
    pushd temp_content
    git checkout -f "$PACKIT_SOURCE_SHA"
    popd
  fi

  # patch VM-using code to not sync disk unless necessary
  sed -e 's/cache=none/cache=unsafe/' -e 's/,io=native//' -i "$CONTEST_DIR/lib/virt.py"
fi

# build the PR's code in a secured container (may be malicious)
rm -f temp_content/custom_build.sh
cat > temp_content/custom_build.sh <<'EOF'
#!/bin/bash
set -x -e
stream=$1
cd /content_to_build
dnf -y install python-srpm-macros
dnf -y builddep --spec scap-security-guide.spec
rm -rf build
mkdir build
cd build
# defaults used by Contest (and scap-security-guide.spec),
# plus any build options needed by any tests (so the tests don't have to rebuild
# the content to add these options)
cmake ../ \
  -DCMAKE_BUILD_TYPE:STRING=Release \
  -DSSG_CENTOS_DERIVATIVES_ENABLED:BOOL=ON \
  -DSSG_PRODUCT_DEFAULT:BOOL=OFF \
  "-DSSG_PRODUCT_RHEL${stream}:BOOL=ON" \
  -DSSG_SCE_ENABLED:BOOL=ON \
  -DSSG_BASH_SCRIPTS_ENABLED:BOOL=OFF \
  -DSSG_BUILD_DISA_DELTA_FILES:BOOL=OFF \
  -DSSG_SEPARATE_SCAP_FILES_ENABLED:BOOL=OFF \
  -DSSG_ANSIBLE_PLAYBOOKS_PER_RULE_ENABLED:BOOL=ON
make -j4
# clean up useless metadata
rm -rf jinja2_cache
EOF
chmod +x temp_content/custom_build.sh

# prepare podman
podman pull registry.fedoraproject.org/fedora

# start build for all streams in the background
containers=()
for var in "${!CONTENT_DIR_@}"; do
  stream="${var#CONTENT_DIR_}"
  dir=${!var}
  mkdir -p "$dir"
  cp -r temp_content/. "$dir/."
  c=$(
    podman container run -d --pull=never --name "cs$stream" \
    --security-opt=no-new-privileges \
    -v "$dir":/content_to_build:Z \
    fedora \
    /content_to_build/custom_build.sh "$stream"
  )
  containers+=("$c")
done

# wait for all of them
podman wait "${containers[@]}"

# and check success
failed=
for c in "${containers[@]}"; do
  podman container logs "$c"
  rc=$(podman inspect "$c" --format '{{.State.ExitCode}}')
  if [[ $rc -ne 0 ]]; then
    failed=1
    echo "build inside container failed"
  fi
done

podman rm "${containers[@]}"

[[ $failed ]] && exit 1 || exit 0
