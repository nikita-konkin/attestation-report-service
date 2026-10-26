"""Take python-attestation-engine out of ssl-nginx-docker/docker-compose.yaml: the service block,
its address in java-gateway and the volume declaration (the volume itself stays — this service
now mounts it from its own compose, see deploy/server.yml).

usage: unpatch_compose.py SOURCE TARGET; writes TARGET, unchanged when the service is already gone.
"""

import re
import sys
from pathlib import Path

GATEWAY_LINE = "            - PYTHON_ATTESTATION_ENGINE_URL=http://python-attestation-engine:8002\n"
VOLUME_LINE = "    attestation-data:\n"


def unpatch(text: str) -> str:
    if not re.search(r"^    python-attestation-engine:$", text, re.M):
        print("python-attestation-engine в compose уже нет — оставляю как есть")
        return text
    lines = text.splitlines(keepends=True)
    start = lines.index("    python-attestation-engine:\n")
    # комментарий над сервисом уходит вместе с ним
    while start > 0 and lines[start - 1].startswith("    #"):
        start -= 1
    end = next(
        number for number in range(lines.index("    python-attestation-engine:\n") + 1, len(lines))
        if re.match(r"^    \S", lines[number]) or re.match(r"^\S", lines[number])
    )
    lines[start:end] = []
    text = "".join(lines).replace(GATEWAY_LINE, "")
    text = re.sub(r"^(volumes:\n(?:    \S.*\n)*?)" + re.escape(VOLUME_LINE), r"\1", text, flags=re.M)
    print("compose: убран python-attestation-engine, его адрес у шлюза и объявление тома")
    return text


if __name__ == "__main__":
    source, target = sys.argv[1:3]
    Path(target).write_text(unpatch(Path(source).read_text(encoding="utf-8")), encoding="utf-8")
