#!/usr/bin/env bash
# Включает автообновление (CD) на сервере сайта: после слияния в main сервер сам собирает
# и перезапускает сервис в течение 5 минут (см. deploy/autoupdate.sh).
#   bash ~/projects/attestation-report-service/deploy/install-autoupdate.sh
# Спросит пароль sudo один раз — дальше обновления пароля не требуют. Повторный запуск безопасен.
# Выключить: sudo systemctl disable --now attestation-autoupdate.timer
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
UNITS=/etc/systemd/system

sudo -v
for unit in attestation-autoupdate.service attestation-autoupdate.timer; do
    sed "s|@REPO@|$REPO|g" "$HERE/systemd/$unit" | sudo tee "$UNITS/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now attestation-autoupdate.timer
# первая проверка — сразу: если main новее того, что работает, обновление начнётся сейчас
sudo systemctl start --no-block attestation-autoupdate.service

echo
systemctl list-timers attestation-autoupdate.timer --no-pager
echo
echo "Автообновление включено: новый коммит в main попадает на сервер в течение 5 минут."
echo "Журнал: journalctl -u attestation-autoupdate -f"
