#!/bin/bash
        
set -e -x

if cat /proc/swaps | grep zram0; then
  swapoff /dev/zram0
  zramctl --reset /dev/zram0
fi

# because the default XFS/btrfs is super slow over Amazon EC2 EBS,
# create a fast zram-backed block device with XFS on top, and use it
# for image storage
# - tmpfs can't be used, it doesn't support reflink, and we get compression
#   via zram this way too
modprobe zram
zdev=$(zramctl --find --size 300G --algorithm lz4)
mkfs.xfs -m reflink=1 "$zdev"

if [[ -d /var/lib/containers ]]; then
  # move over original /var/lib/containers
  mv /var/lib/containers{,.bak}
  mkdir /var/lib/containers
  mount -o discard "$zdev" /var/lib/containers
  for tool in chmod chown chcon; do
    "$tool" --reference=/var/lib/containers.bak /var/lib/containers  # the dir itself
  done
  cp -a /var/lib/containers.bak/. /var/lib/containers/
  rm -rf /var/lib/containers.bak
else
  mkdir -p /var/lib/containers
  mount -o discard "$zdev" /var/lib/containers
  restorecon -vF /var/lib/containers
fi
