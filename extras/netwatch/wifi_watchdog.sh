#!/bin/sh
# Bring the Wi-Fi back when NetworkManager has given up on it.
# NetworkManager 1.42 stops auto-connecting after a long run of refused associations
# (it decides the password is wrong, reason 'no-secrets') and never retries on its own,
# so the Pi stays offline after the router recovers. Runs every 2 minutes from a timer;
# does nothing while wlan0 is connected or while the wired port has a connection.
CONN=$(nmcli -g NAME,TYPE connection show | grep ':802-11-wireless$' | head -1 | cut -d: -f1)
[ "$(nmcli -g GENERAL.STATE device show eth0 2>/dev/null | cut -d' ' -f1)" = 100 ] && exit 0
[ "$(nmcli -g GENERAL.STATE device show wlan0 | cut -d' ' -f1)" = 100 ] && exit 0
# still connecting on its own: leave it alone
STATE=$(nmcli -g GENERAL.STATE device show wlan0)
case "$STATE" in 4[0-9]*|5[0-9]*|6[0-9]*|7[0-9]*|8[0-9]*|90*) echo "wlan0 busy: $STATE"; exit 0;; esac
echo "wlan0 not connected ($STATE), bringing up $CONN"
nmcli -w 60 connection up "$CONN" && echo "reconnected" || echo "reconnect failed, will retry"
