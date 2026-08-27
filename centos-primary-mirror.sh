#!/bin/bash

# all this is copy/pasted from ATEX TestingFarmProvisioner (cleaning.sh)

os_version=$(. /etc/os-release; echo "$VERSION_ID")

# don't use gpgkey= repo options, import instead, because:
#   warning: Signature not supported. Hash algorithm SHA1 not available.
#   Key import failed (code 2). Failing package is: NetworkManager-1:1.54.4-3.el9.x86_64
#    GPG Keys are configured as: file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-SIG-Extras, file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-SIG-Extras-SHA512, file:///etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial, file:///etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial-PQC

shopt -s nullglob
for key in /etc/pki/rpm-gpg/RPM-GPG-KEY-*; do
    rpm --import "$key"
done
shopt -u nullglob

function mkrepo {
    echo "[$1]"
    echo "name=$1"
    echo "baseurl=$2"
    echo "gpgcheck=1"
    local additional
    for additional in "${@:3}"; do
        echo "$additional"
    done
}       
# replace fedora mirrormanager-based repositories with primary/master ones,
# which tend to be a lot more reliable
# - this is to avoid checksum errors that very commonly pop up on mirrormanager
#   on all mirrors (so trying different mirrors doesn't help and dnf eventually
#   fails): 
#     Downloading successful, but checksum doesn't match. Calculated: 1abb62...
#     Expected: a91641...
if [[ $os_version == 9 || $os_version == 10 ]]; then
    rm -f /etc/yum.repos.d/*
    case "$os_version" in
        9)  variants="BaseOS AppStream CRB HighAvailability NFV RT ResilientStorage" ;;
        10) variants="BaseOS AppStream CRB HighAvailability NFV RT" ;;
    esac    
    rm -f /etc/yum.repos.d/centos{,-addons}.repo
    for variant in $variants; do
        mkrepo "centos-master-$variant" "https://mirror.stream.centos.org/\$stream/$variant/\$basearch/os/" enabled=1
        mkrepo "centos-master-$variant-source" "https://mirror.stream.centos.org/\$stream/$variant/source/tree/" enabled=0
        mkrepo "centos-master-$variant-debuginfo" "https://mirror.stream.centos.org/\$stream/$variant/\$basearch/debug/tree/" enabled=0
        echo
    done > /etc/yum.repos.d/centos-master.repo

# ... except for CS7/CS8, which is on vault
elif [[ $os_version -le 8 ]]; then
    if grep -q 'mirror\.centos\.org' /etc/yum.repos.d/CentOS-*.repo; then
        sed -i \
            -e 's/^mirrorlist/#mirrorlist/' \
            -e 's/^#baseurl/baseurl/' \
            -e 's/mirror\.centos\.org/vault.centos.org/' \
            /etc/yum.repos.d/CentOS-*.repo
    fi
fi
