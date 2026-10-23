#!/bin/sh
echo "[*] Installing Homebrew Channel to LG TV..."
mkdir -p /media/developer/temp
if [ ! -f /media/developer/temp/hbchannel.ipk ]; then
    echo "[*] Downloading hbchannel.ipk..."
    curl -kL -o /media/developer/temp/hbchannel.ipk https://github.com/webosbrew/webos-homebrew-channel/releases/download/v0.7.3/org.webosbrew.hbchannel_0.7.3_all.ipk
fi

luna-send-pub -i -f luna://com.webos.appInstallService/dev/install '{"id":"org.webosbrew.hbchannel","ipkUrl":"/media/developer/temp/hbchannel.ipk","subscribe":true}'
echo "[*] Installation command sent!"
