#! /bin/bash
apt-get update
apt-get upgrade -y
apt-get install -y git
sudo mkdir -p /mnt/pd0
sudo /usr/share/google/safe_format_and_mount -m "mkfs.ext4 -F" /dev/disk/by-id/google-skia-perf-data /mnt/pd0
sudo chmod 777 /mnt/pd0
