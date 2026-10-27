#!/usr/bin/env bash
# Автообновление (CD) на 192.168.50.117: таймер attestation-autoupdate.timer раз в 5 минут
# запускает этот сценарий от root. Если в origin/main появился коммит, которого ещё нет на сервере,
# идёт deploy/update.sh — та же сборка с тестами и та же проверка через nginx, что и вручную.
# Коммит, который не собрался, не повторяется: следующая попытка — со следующим коммитом в main.
# Журнал: journalctl -u attestation-autoupdate
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
STATE=/var/lib/attestation-autoupdate
OWNER=$(stat -c %U "$REPO")

[ "$(id -u)" = 0 ] || { echo "запускается таймером от root"; exit 1; }
mkdir -p "$STATE"

# сеть или GitHub недоступны — ничего не делать до следующего раза
if ! remote=$(runuser -u "$OWNER" -- git -C "$REPO" ls-remote -q origin refs/heads/main | cut -f1) || [ -z "$remote" ]; then
    echo "origin недоступен — попробую позже"
    exit 0
fi
[ "$remote" = "$(cat "$STATE/deployed" 2>/dev/null)" ] && exit 0
[ "$remote" = "$(cat "$STATE/failed" 2>/dev/null)" ] && exit 0

echo "в main новый коммит ${remote:0:7} — обновляю"
status=0
bash "$HERE/update.sh" main || status=$?
case $status in
    0)
        runuser -u "$OWNER" -- git -C "$REPO" rev-parse HEAD > "$STATE/deployed"
        rm -f "$STATE/failed"
        ;;
    75)
        echo "идёт обновление вручную — проверю в следующий раз"
        ;;
    *)
        echo "$remote" > "$STATE/failed"
        echo "обновление до ${remote:0:7} не удалось (код $status); прежний контейнер работает"
        exit "$status"
        ;;
esac
