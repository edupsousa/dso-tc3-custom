#!/bin/sh
# Run a command so that libserialport accepts a pty as a serial port (no root needed).
# usage: with_fake_tty.sh <pty path> <ptyshim.so> <command...>
# A user + mount namespace gets a fake /sys/class/tty/pts/N entry; LD_PRELOAD answers the modem ioctls.
pty=$1; shim=$2; shift 2
n=${pty##*/}
exec unshare -rm sh -c '
	mount -t tmpfs none /sys/class/tty &&
	mkdir -p /sys/class/tty/pts/'"$n"'/dev1 && ln -s dev1 /sys/class/tty/pts/'"$n"'/device &&
	LD_PRELOAD='"$shim"' exec "$@"' sh "$@"
