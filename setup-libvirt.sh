#!/bin/bash

set -e -x

# (6GB per VM + 1GB for QEMU itself) * 36 = 252 GB
# 252 + 100G zram = 352 GB, leaving ~30G for the host
TOTAL_VMS=36

# disallow qemu network bridge helper use by non-root (just in case)
# - all domains should use passt/SLIRP
echo -n > /etc/qemu/bridge.conf

# because the default XFS/btrfs is super slow over Amazon EC2 EBS,
# create a fast zram-backed block device with XFS on top, and use it
# for image storage
# - tmpfs can't be used, it doesn't support reflink, and we get compression
#   via zram this way too
modprobe zram
zdev=$(zramctl --find --size 100G --algorithm lz4)
mkfs.xfs -m reflink=1 "$zdev"
mv /var/lib/libvirt{,.bak}
mkdir /var/lib/libvirt
mount "$zdev" /var/lib/libvirt
for tool in chmod chown chcon; do
  "$tool" --reference=/var/lib/libvirt.bak /var/lib/libvirt  # the dir itself
done
cp -a /var/lib/libvirt.bak/. /var/lib/libvirt/
rm -rf /var/lib/libvirt.bak

systemctl enable --now \
  virtqemud.socket virtstoraged.socket

# set up storage pool XML
# - this can be done offline (without 'virsh pool-define' as long as a specific
#   file naming format is followed (filename as entity name)
cat > xml <<EOF
<pool type="dir">
  <name>default</name>
  <target>
    <path>/var/lib/libvirt/images</path>
  </target>
</pool>
EOF
virsh pool-define xml
virsh pool-autostart default
virsh pool-start default

# also add one for virt-install uploads, so it doesn't have to
# create it on first run (opening us to race conditions)
cat > xml <<EOF
<pool type="dir">
  <name>boot-scratch</name>
  <target>
    <path>/var/lib/libvirt/boot</path>
  </target>
</pool>
EOF
virsh pool-define xml
virsh pool-autostart boot-scratch
virsh pool-start boot-scratch

# create VMs
read -r -d '' vm_template <<'EOF' || true
<domain type='kvm'>
  <name>%%%NAME%%%</name>
  <memory unit='GiB'>6</memory>
  <currentMemory unit='GiB'>6</currentMemory>
  <vcpu placement='static'>3</vcpu>
  <os firmware='efi'>
    <type arch='x86_64' machine='q35'>hvm</type>
    <loader secure='no'/>
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
    <smm state='on'/>
  </features>
  <cpu mode='host-passthrough'></cpu>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>
  <devices>
    <disk type='volume' device='disk'>
      <driver name='qemu' type='raw' cache='none' io='native' discard='unmap'/>
      <source pool='default' volume='%%%NAME%%%'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <interface type='user'>
      <backend type='passt'/>
      <model type='virtio'/>
      <ip address='100.80.60.1' family='ipv4' prefix='24'/>
      <portForward proto='tcp' address='0.0.0.0'>
        <range start='%%%PORT%%%' to='22'/>
      </portForward>
    </interface>
    <console type='pty'>
      <target type='serial'/>
    </console>
    <rng model='virtio'>
      <backend model='random'>/dev/urandom</backend>
    </rng>
  </devices>
</domain>
EOF
for i in $(seq 1 $TOTAL_VMS); do
  vm_name=vm$i
  vm_port=$(( 5000 + i ))
  truncate -s 10M "/var/lib/libvirt/images/$vm_name" # size doesn't matter
  chmod 0600 "/var/lib/libvirt/images/$vm_name"
  sed -e "s/%%%NAME%%%/$vm_name/g" -e "s/%%%PORT%%%/$vm_port/g" \
    <<<"$vm_template" > xml
  virsh define xml
done

rm -f xml

# log kvm kernel parameters (ie. nested virt)
grep -r . /sys/module/kvm*/parameters || true
