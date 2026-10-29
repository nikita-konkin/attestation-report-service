#!/usr/bin/env bash
# Первая установка на сервере сайта (от имени пользователя с sudo): сервис уходит из ssl-nginx-docker в свой
# репозиторий и свой compose, nginx начинает вести /attestation прямо на него, без java-gateway.
#   git clone https://github.com/nikita-konkin/attestation-report-service.git ~/projects/attestation-report-service
#   bash ~/projects/attestation-report-service/deploy/install.sh
# Спросит пароль sudo. Повторный запуск безопасен: сделанные шаги пропускаются.
# Любая ошибка останавливает сценарий; nginx при неудаче возвращается к прежнему конфигу.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
NGINX=$HOME/projects/ssl-nginx-docker
OLD_CONTAINER=diploma-python-attestation-engine
CONTAINER=attestation-report
VOLUME=${ATTESTATION_VOLUME:-ssl-nginx-docker_attestation-data}
DOMAIN=xn----etb9agicel.xn--p1ai
STAMP=$(date +%Y%m%d-%H%M%S)
COMPOSE=(sudo docker compose -f "$REPO/docker-compose.yml" -f "$REPO/deploy/server.yml")

step() { printf '\n== %s\n' "$*"; }
# пока контейнеры перезапускаются, curl не соединяется: код тогда 000, а не остановка по set -e
site() { curl -sk -o /dev/null -m 10 -w '%{http_code}' -H "Host: $DOMAIN" "https://127.0.0.1$1" || true; }

step "0/6 Права sudo"
sudo -v

step "1/6 Том с направлениями кафедр: $VOLUME"
if sudo docker volume inspect "$VOLUME" >/dev/null 2>&1; then
    echo "есть — данные переезжают вместе с сервисом"
else
    sudo docker volume create "$VOLUME" >/dev/null
    echo "не было — создан пустой"
fi

step "2/6 Сборка и запуск attestation-report (несколько минут: при сборке идут тесты)"
cd "$REPO"
"${COMPOSE[@]}" up -d --build
for _ in $(seq 1 60); do
    health=$(sudo docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || true)
    [ "$health" = healthy ] && break
    sleep 2
done
[ "$health" = healthy ] || { echo "контейнер $CONTAINER не стал healthy ($health) — nginx не трогаю"; exit 1; }
echo "$CONTAINER: healthy"

step "3/6 nginx: /attestation прямо на сервис"
cd "$NGINX"
python3 "$HERE/patch_nginx.py" nginx/nginx.conf nginx/nginx.conf.new
# новый конфиг проверяется в отдельном контейнере, прежде чем попасть в рабочий
sudo docker run --rm --network mynetwork \
    -v "$NGINX/nginx/nginx.conf.new:/etc/nginx/nginx.conf:ro" \
    -v "$NGINX/certbot/conf:/etc/letsencrypt:ro" \
    -v "$NGINX/nginx/opendiplom.htpasswd:/etc/nginx/opendiplom.htpasswd:ro" \
    nginx nginx -t
cp -p nginx/nginx.conf "nginx/nginx.conf.bak-$STAMP"
# запись в тот же файл: nginx.conf смонтирован в контейнер по inode
cat nginx/nginx.conf.new > nginx/nginx.conf
rm -f nginx/nginx.conf.new
if ! { sudo docker exec nginx nginx -t && sudo docker exec nginx nginx -s reload; }; then
    echo "nginx не принял конфиг — возвращаю прежний"
    cat "nginx/nginx.conf.bak-$STAMP" > nginx/nginx.conf
    sudo docker exec nginx nginx -s reload
    exit 1
fi

step "4/6 Прежний контейнер $OLD_CONTAINER"
if sudo docker ps -a --format '{{.Names}}' | grep -qx "$OLD_CONTAINER"; then
    sudo docker rm -f "$OLD_CONTAINER" >/dev/null
    echo "остановлен и удалён (том с данными остаётся)"
else
    echo "его уже нет"
fi

step "5/6 ssl-nginx-docker: сервис больше не собирается оттуда"
python3 "$HERE/unpatch_compose.py" docker-compose.yaml docker-compose.yaml.new
docker compose -f docker-compose.yaml.new config -q
if ! cmp -s docker-compose.yaml docker-compose.yaml.new; then
    cp -p docker-compose.yaml "docker-compose.yaml.bak-$STAMP"
    cat docker-compose.yaml.new > docker-compose.yaml
fi
rm -f docker-compose.yaml.new

step "6/6 Проверка через nginx"
page=000
for _ in $(seq 1 30); do
    page=$(site /attestation)
    [ "$page" = 200 ] && break
    sleep 2
done
charts=$(site /attestation/static/charts.js)
health=$(site /health)
office=$(sudo docker exec "$CONTAINER" soffice --version 2>/dev/null | head -1 || true)
echo "/attestation: $page, charts.js: $charts, /health сайта: $health (ожидается 200)"
echo "LibreOffice в контейнере: ${office:-не найден — PDF собираться не будут}"
[ "$page" = 200 ] && [ "$charts" = 200 ] && [ "$health" = 200 ] || { echo "что-то не так — см. выше"; exit 1; }

echo
echo "Готово: https://пгту-ртф.рф/attestation отдаёт $CONTAINER напрямую."
echo "Дальше обновления — bash $REPO/deploy/update.sh"
if [ -f "$NGINX/nginx/nginx.conf.bak-$STAMP" ]; then
    echo "Откат nginx: cat $NGINX/nginx/nginx.conf.bak-$STAMP > $NGINX/nginx/nginx.conf && sudo docker exec nginx nginx -s reload"
fi
if [ -f "$NGINX/docker-compose.yaml.bak-$STAMP" ]; then
    echo "Откат compose: cat $NGINX/docker-compose.yaml.bak-$STAMP > $NGINX/docker-compose.yaml"
fi
