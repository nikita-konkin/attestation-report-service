#!/usr/bin/env bash
# Обновление «Отчёта по аттестации» на 192.168.50.117 после установки (deploy/install.sh):
#   bash ~/projects/attestation-report-service/deploy/update.sh            # ветка main
#   bash ~/projects/attestation-report-service/deploy/update.sh my-branch  # другая ветка
# Спросит пароль sudo. Основной сервис сайта и шлюз не пересобираются.
set -euo pipefail

BRANCH=${1:-main}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
CONTAINER=attestation-report
DOMAIN=xn----etb9agicel.xn--p1ai

step() { printf '\n== %s\n' "$*"; }
# пока контейнер перезапускается, curl не соединяется: код тогда 000, а не остановка по set -e
site() { curl -sk -o /dev/null -m 10 -w '%{http_code}' -H "Host: $DOMAIN" "https://127.0.0.1$1" || true; }

step "0/3 Права sudo"
sudo -v

step "1/3 Код → origin/$BRANCH"
if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
    echo "в $REPO есть незакоммиченные изменения — остановка"; exit 1
fi
PREVIOUS=$(git -C "$REPO" rev-parse --short HEAD)
git -C "$REPO" fetch -q origin
git -C "$REPO" checkout -q -B "$BRANCH" "origin/$BRANCH"
echo "было: $PREVIOUS, стало: $(git -C "$REPO" log --oneline -1)"

step "2/3 Сборка и перезапуск (несколько минут: при сборке идут тесты)"
cd "$REPO"
if ! sudo docker compose -f docker-compose.yml -f deploy/server.yml up -d --build; then
    echo "сборка не удалась — прежний контейнер работает."
    echo "Вернуть код: git -C $REPO checkout -q -B main $PREVIOUS"
    exit 1
fi

step "3/3 Проверка через nginx"
page=000
for _ in $(seq 1 60); do
    page=$(site /attestation)
    [ "$page" = 200 ] && break
    sleep 2
done
charts=$(site /attestation/static/charts.js)
office=$(sudo docker exec "$CONTAINER" soffice --version 2>/dev/null | head -1 || true)
echo "/attestation: $page, charts.js: $charts (ожидается 200)"
echo "LibreOffice в контейнере: ${office:-не найден — PDF собираться не будут}"
[ "$page" = 200 ] && [ "$charts" = 200 ] || { echo "что-то не так — см. выше"; exit 1; }

echo
echo "Готово: https://пгту-ртф.рф/attestation"
echo "Откат: git -C $REPO checkout -q -B main $PREVIOUS && cd $REPO && sudo docker compose -f docker-compose.yml -f deploy/server.yml up -d --build"
