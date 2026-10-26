"""Add location /attestation (deploy/nginx-location.conf) to the HTTPS block of пгту-ртф.рф.

usage: patch_nginx.py SOURCE TARGET; writes TARGET, unchanged when the location is already there.
"""

import sys
from pathlib import Path

DOMAIN = "server_name пгту-ртф.рф xn----etb9agicel.xn--p1ai;"
MARK = "location /attestation"
BLOCK = (Path(__file__).parent / "nginx-location.conf").read_text(encoding="utf-8")


def patch(text: str) -> str:
    if MARK in text:
        print("location /attestation уже есть — оставляю как есть")
        return text
    lines = text.splitlines(keepends=True)
    names = [number for number, line in enumerate(lines) if line.strip() == DOMAIN]
    if len(names) != 1:
        sys.exit(f"ожидался один блок с «{DOMAIN}», найдено: {len(names)}")
    start = names[0]
    opening = max(number for number in range(start) if lines[number].strip() == "server {")
    if not any("listen 443" in lines[number] for number in range(opening, start)):
        sys.exit("блок домена не слушает 443 — конфиг не тот, что ожидался")
    for number in range(start, len(lines)):
        if lines[number].strip() == "location / {":
            # перед комментарием к «location /», если он есть
            at = number - 1 if lines[number - 1].strip().startswith("#") else number
            print("nginx: добавлен location /attestation")
            return "".join(lines[:at] + [BLOCK] + lines[at:])
        if lines[number].strip() == "server {":
            break
    sys.exit("в блоке домена не найдено «location / {»")


if __name__ == "__main__":
    source, target = sys.argv[1:3]
    Path(target).write_text(patch(Path(source).read_text(encoding="utf-8")), encoding="utf-8")
