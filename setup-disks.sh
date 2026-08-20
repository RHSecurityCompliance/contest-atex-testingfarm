#!/bin/bash

set -e -x

# ensure we are on z1d metal with two unused NVMes
empty_disks=()

for disk in /dev/nvme[0-9]n1; do
  if ! out=$(sfdisk -d "$disk" 2>&1); then
    if [[ $out == *does\ not\ contain\ a\ recognized\ partition\ table* ]]; then
      empty_disks+=("$disk")
    fi
  fi
done
if [[ ${#empty_disks[@]} -lt 2 ]]; then
  echo "found <2 disks without partition tables (not on z1d metal AWS?)"
  exit 1
fi

# set them up in a RAID0
mdadm --create /dev/md/bigstore --level=0 --raid-devices=2 "${empty_disks[@]}"
mkfs.xfs /dev/md/bigstore

if [[ -d /var/lib/containers ]]; then
  # move over original /var/lib/containers
  mv /var/lib/containers{,.bak}
  mkdir /var/lib/containers
  mount /dev/md/bigstore /var/lib/containers
  for tool in chmod chown chcon; do
    "$tool" --reference=/var/lib/containers.bak /var/lib/containers  # the dir itself
  done
  cp -a /var/lib/containers.bak/. /var/lib/containers/
  rm -rf /var/lib/containers.bak
else
  mkdir -p /var/lib/containers
  mount /dev/md/bigstore /var/lib/containers
  restorecon -vF /var/lib/containers
fi
