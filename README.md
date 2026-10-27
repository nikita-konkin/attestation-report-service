# attestation-report-service

[![CI](https://github.com/nikita-konkin/attestation-report-service/actions/workflows/ci.yml/badge.svg)](https://github.com/nikita-konkin/attestation-report-service/actions/workflows/ci.yml)

«Отчёт по аттестации» на сайте [пгту-ртф.рф/attestation](https://xn----etb9agicel.xn--p1ai/attestation):
отчёты об аттестации групп кафедры по данным портала РИТМ. Вход — учётной записью РИТМ, своих
учётных записей у сервиса нет.

Сервис отдельный от основного сайта ([diploma_supplement_service](https://github.com/nikita-konkin/diploma_supplement_service)),
домен общий: nginx сайта ведёт `/attestation` прямо в контейнер `attestation-report`
([deploy/nginx-location.conf](deploy/nginx-location.conf)), всё остальное — в основной сервис.
До разделения сервис жил в основном репозитории в папке `python-attestation-engine`; история коммитов сохранена.

## Что внутри

- `app/main.py` — маршруты `/attestation/...`: вход, страница, списки факультетов, кафедр и групп, задачи, файлы.
- `app/portal.py` — всё, что сервис спрашивает у портала; в тестах заменяется подделкой.
- `app/security.py` — сеансы (в памяти, с сеансом портала вместо пароля) и ограничение входов.
- `app/presets.py` — запомненные направления групп каждой кафедры (`departments.json` в `ATTESTATION_DATA_DIR`).
- `app/jobs.py` — фоновые задачи: выгрузка идёт минутами, страница опрашивает состояние; файлы удаляются по таймеру.
- `app/shares.py` — ссылки на результаты отчёта без входа: копия отчёта на срок ссылки (1, 3 или 7 дней).
- `app/static/` — страницы без встроенных скриптов (CSP `script-src 'self'`).
  `report.js` показывает готовый отчёт вкладками — и на странице сервиса (`app.js`), и на странице по ссылке (`share.js`);
  `charts.js` рисует плитки, график Н/А по группам и тепловые карты из `attestation_report.json` задачи;
  оформление «Чертёж» (`style.css`) — лист с рамкой и основной надписью, шрифты только установленные.
- `reports/` — модули отчёта, те же, что запускаются из командной строки (`make_report.py --att 1`).
  Это копия рабочей папки анализа: правки вносятся там и переносятся сюда целиком.
  Кафедра, факультет и заведующий для шапки берутся из `department.json` в папке аттестации; без него — ИТРС.
  Данные для графиков `report_model.write_report_data` пишет в `attestation_report.json`; для динамики `make_report.fetch_previous` догружает предыдущую аттестацию того же семестра.
  `pdf_export.py` делает PDF из каждого DOCX через LibreOffice (он есть в образе) и подбирает сжатие картинок так,
  чтобы файл не превышал 2 МБ; без LibreOffice отчёты остаются в DOCX.

## Отчёт по группам и ссылки

- Отчёт по направлениям берёт все группы отмеченных направлений, в том числе новые, и запоминает выбор для кафедры.
  Отчёт «только отмеченные группы» строится по одной или нескольким группам: в нём нет того, что описывает кафедру
  целиком (места кафедры среди кафедр вуза и раздела о преподавателях кафедры в подробном отчёте), и он ничего
  не запоминает.
- Готовым отчётом можно поделиться: ссылка `/attestation/share#…` открывает его разделы, графики и файлы без входа,
  пока не истечёт срок (1, 3 или 7 дней) или преподаватель не отзовёт её в списке своих ссылок. Ссылка может быть
  постоянной — сразу или позже кнопкой «Сделать постоянной» в списке, пока срочная ссылка не истекла; постоянная
  открывается до отзыва. Истёкшую ссылку вернуть нельзя: её копия отчёта уже удалена.

## Защита

- пароль РИТМ используется один раз для входа в портал и не сохраняется; сеанс портала живёт в памяти и забывается при выходе или после 30 минут бездействия;
- cookie `HttpOnly; Secure; SameSite=Strict; Path=/attestation`, изменяющие запросы требуют CSRF-токен;
- неудачные входы ограничены: 5 на клиента и 30 всего за 15 минут; адрес клиента — последний в `X-Forwarded-For`, его дописывает nginx;
- CSP `default-src 'none'`, без встроенных скриптов и стилей; пароли не попадают в журналы;
- выгруженные данные содержат ФИО и баллы студентов, поэтому файлы удаляются через `ATTESTATION_FILES_TTL` секунд (по умолчанию 30 минут);
- ключ ссылки (256 бит) стоит в адресе после `#`: браузер не отправляет эту часть на сервер, поэтому ключ не попадает
  в журналы nginx и в Referer, страница передаёт его заголовком `X-Share-Token`; на диске лежит только SHA-256 ключа,
  поэтому сами файлы ссылку не выдают; копия отчёта (с ФИО и баллами) лежит в томе данных и удаляется по истечении
  срока или при отзыве, а у постоянной ссылки — только при отзыве; неверные ключи ограничены 30 на клиента за 15 минут;
- реальных данных студентов в репозитории нет: тесты строят выгрузки в памяти (`tests/synthetic.py`), а `.gitignore` не пускает XML, DOCX, PDF и картинки.

## Запуск

Без Docker:

```sh
python -m venv .venv && .venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest -q
PYTHONPATH=.:reports ATTESTATION_COOKIE_SECURE=0 ATTESTATION_DATA_DIR=./data \
  .venv/bin/uvicorn app.main:app --port 8002
```

В Docker, на http://localhost:8002/attestation (тесты идут при сборке образа):

```sh
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build
```

Тесты не обращаются к порталу и не используют реальные данные. Тесты PDF требуют LibreOffice
и без него пропускаются; в образе и в CI он есть.

## CI

[GitHub Actions](.github/workflows/ci.yml) на каждый PR и push в `main`: проверяет compose и скрипты
развёртывания и собирает образ — вместе с ним проходят все тесты, как при сборке на сервере.

## Сервер

192.168.50.117, папка `~/projects/attestation-report-service`, контейнер в сети `mynetwork` рядом с nginx
сайта ([deploy/server.yml](deploy/server.yml)). Первая установка переносит сервис из `ssl-nginx-docker`
и переключает nginx ([deploy/install.sh](deploy/install.sh)):

```sh
ssh -t liam@192.168.50.117 'git clone https://github.com/nikita-konkin/attestation-report-service.git ~/projects/attestation-report-service && bash ~/projects/attestation-report-service/deploy/install.sh'
```

Дальше — обновление с `main` ([deploy/update.sh](deploy/update.sh)), основной сайт при этом не пересобирается:

```sh
ssh -t liam@192.168.50.117 'bash ~/projects/attestation-report-service/deploy/update.sh'
```
