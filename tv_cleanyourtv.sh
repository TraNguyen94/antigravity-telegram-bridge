#!/bin/sh
echo "[*] Downloading CleanYourTV Manager..."
mkdir -p /media/developer/temp
curl -kL -o /media/developer/temp/cleanyourtv.ipk https://github.com/lasharor/cleanyourtv-WebOS/releases/download/v1.5.3/com.cleanyourtv.manager_1.5.3_all.ipk

echo "[*] Installing CleanYourTV Manager..."
luna-send-pub -i -f luna://com.webos.appInstallService/dev/install '{"id":"com.cleanyourtv.manager","ipkUrl":"/media/developer/temp/cleanyourtv.ipk","subscribe":true}'
echo "[*] Done! Check your TV apps!"
