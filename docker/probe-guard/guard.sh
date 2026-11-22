#!/bin/sh
# Applied before the probe starts. The probe has no capabilities, so it cannot change them.
set -eu

chown "${FTF_UID:-1000}:${FTF_UID:-1000}" /sandbox

iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT  # Docker's DNS resolver lives on loopback
for net in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 \
           192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 \
           224.0.0.0/4 240.0.0.0/4; do
  iptables -A OUTPUT -d "$net" -j REJECT
done

touch /tmp/ready
trap 'exit 0' TERM INT
while :; do sleep 1; done
