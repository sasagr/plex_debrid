#!/bin/sh
# Bring the Wi-Fi back when NetworkManager has given up on it.
# NetworkManager 1.42 stops auto-connecting after a long run of refused associations
# (it decides the password is wrong, reason 'no-secrets') and never retries on its own,
# so the Pi stays offline after the router recovers. While the router refuses it, the Pi may
# also join a weak access point with the same name (an extender) and never move back, so a
# connected Pi on a weak signal is roamed to a much stronger access point of the same network.
# Runs every 2 minutes from a timer; does nothing while the wired port has a connection.
CONN=$(nmcli -g NAME,TYPE connection show | grep ':802-11-wireless$' | head -1 | cut -d: -f1)
[ "$(nmcli -g GENERAL.STATE device show eth0 2>/dev/null | cut -d' ' -f1)" = 100 ] && exit 0
WEAK=-70        # dBm: below this the current access point counts as weak
BETTER=15       # dB: a candidate must be at least this much stronger, and no weaker than -65 dBm
if [ "$(nmcli -g GENERAL.STATE device show wlan0 | cut -d' ' -f1)" = 100 ]; then
    CUR=$(iw dev wlan0 link | awk '/^Connected to/{print $3}')
    SIG=$(iw dev wlan0 link | awk '/signal:/{print int($2)}')
    SSID=$(iw dev wlan0 link | sed -n 's/^[[:space:]]*SSID: //p')
    [ -z "$SIG" ] || [ "$SIG" -ge "$WEAK" ] && exit 0
    iw dev wlan0 scan >/dev/null 2>&1   # refresh the list; a failed scan falls back to the cached one
    BEST=$(iw dev wlan0 scan dump | awk -v ssid="$SSID" '
        /^BSS /{b=substr($2,1,17); s=""} /signal:/{s=int($2)} /^[[:space:]]*SSID: /{n=substr($0,index($0,"SSID: ")+6); if (n==ssid && s!="") print s, b}' | sort -rn | head -1)
    BSIG=${BEST% *}; BBSSID=${BEST#* }
    if [ -n "$BEST" ] && [ "$BBSSID" != "$CUR" ] && [ "$BSIG" -ge -65 ] && [ "$BSIG" -ge $((SIG + BETTER)) ]; then
        echo "weak access point $CUR ($SIG dBm), roaming to $BBSSID ($BSIG dBm)"
        wpa_cli -i wlan0 roam "$BBSSID" >/dev/null
        sleep 8
        echo "now on $(iw dev wlan0 link | awk '/^Connected to/{print $3}') at $(iw dev wlan0 link | awk '/signal:/{print $2}') dBm"
    fi
    exit 0
fi
# still connecting on its own: leave it alone
STATE=$(nmcli -g GENERAL.STATE device show wlan0)
case "$STATE" in 4[0-9]*|5[0-9]*|6[0-9]*|7[0-9]*|8[0-9]*|90*) echo "wlan0 busy: $STATE"; exit 0;; esac
echo "wlan0 not connected ($STATE), bringing up $CONN"
nmcli -w 60 connection up "$CONN" && echo "reconnected" || echo "reconnect failed, will retry"
