"""Links to report results that open without a login."""

import time

from fastapi.testclient import TestClient
from helpers import RITM_PASSWORD, csrf, finished, log_in, make_service

from app import shares as shares_module
from app.logging_config import configure_logging
from app.shares import ShareStore


def shared_job(tmp_path, days: int = 3, **overrides):
    app, client, portal = make_service(tmp_path, **overrides)
    log_in(client)
    job = finished(client)
    response = client.post(f"/attestation/api/jobs/{job['id']}/share", json={"days": days}, headers=csrf(client))
    return app, client, job, response


def token_of(response) -> str:
    return response.json()["link"].split("#", 1)[1]


def visitor(app) -> TestClient:
    """A browser that never logged in."""
    return TestClient(app, base_url="https://testserver")


def open_share(app, token: str, path: str = "/attestation/api/shared"):
    return visitor(app).get(path, headers={"X-Share-Token": token})


def test_cannot_open_shared_report_without_login(tmp_path):
    app, _, job, response = shared_job(tmp_path)
    assert open_share(app, token_of(response)).json()["markdown"] == job["markdown"], (
        "Shared link did not show the report to a visitor without a login"
    )


def test_cannot_download_shared_file_without_login(tmp_path):
    app, _, _, response = shared_job(tmp_path)
    report = open_share(app, token_of(response)).json()
    href = report["downloads"][0]["files"][0]["href"]
    assert open_share(app, token_of(response), href).content == b"docx", "Shared file did not download by the link"


def test_cannot_open_shared_report_without_token(tmp_path):
    app, _, _, _ = shared_job(tmp_path)
    assert visitor(app).get("/attestation/api/shared").status_code == 404, "Shared report opened without a token"


def test_cannot_open_shared_report_with_wrong_token(tmp_path):
    app, _, _, response = shared_job(tmp_path)
    token = token_of(response)
    wrong = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert open_share(app, wrong).status_code == 404, "Shared report opened with a wrong token"


def test_cannot_download_shared_file_without_token(tmp_path):
    app, _, _, response = shared_job(tmp_path)
    href = open_share(app, token_of(response)).json()["downloads"][0]["files"][0]["href"]
    assert visitor(app).get(href).status_code == 404, "Shared file downloaded without a token"


def test_cannot_put_token_in_link_path(tmp_path):
    _, _, _, response = shared_job(tmp_path)
    assert response.json()["link"].startswith("/attestation/share#"), (
        "Token is not after «#», so it would reach the server logs"
    )


def test_cannot_open_shared_report_after_expiry(tmp_path, monkeypatch):
    app, _, _, response = shared_job(tmp_path, days=1)
    later = time.time() + 24 * 3600 + 1
    monkeypatch.setattr(shares_module.time, "time", lambda: later)
    assert open_share(app, token_of(response)).status_code == 404, "Shared report opened after its link expired"


def test_cannot_open_shared_report_after_revoke(tmp_path):
    app, client, _, response = shared_job(tmp_path)
    client.delete(f"/attestation/api/shares/{response.json()['id']}", headers=csrf(client))
    assert open_share(app, token_of(response)).status_code == 404, "Shared report opened after it was revoked"


def test_cannot_revoke_link_of_another_teacher(tmp_path):
    app, _, _, response = shared_job(tmp_path)
    other = visitor(app)
    other.post("/attestation/login", data={"username": "marstu.net\\other", "password": RITM_PASSWORD})
    other.delete(f"/attestation/api/shares/{response.json()['id']}", headers=csrf(other))
    assert open_share(app, token_of(response)).status_code == 200, "Another teacher revoked someone else's link"


def test_cannot_list_links_of_another_teacher(tmp_path):
    app, _, _, _ = shared_job(tmp_path)
    other = visitor(app)
    other.post("/attestation/login", data={"username": "marstu.net\\other", "password": RITM_PASSWORD})
    assert other.get("/attestation/api/shares").json()["shares"] == [], "Another teacher sees someone else's links"


def test_cannot_lose_link_from_owner_list(tmp_path):
    _, client, _, response = shared_job(tmp_path)
    ids = [share["id"] for share in client.get("/attestation/api/shares").json()["shares"]]
    assert ids == [response.json()["id"]], "The owner's new link is missing from their list"


def test_cannot_show_token_in_owner_list(tmp_path):
    _, client, _, response = shared_job(tmp_path)
    assert token_of(response) not in client.get("/attestation/api/shares").text, (
        "The list of links gives the tokens away"
    )


def test_cannot_share_without_csrf_token(tmp_path):
    _, client, _ = make_service(tmp_path)
    log_in(client)
    job = finished(client)
    response = client.post(f"/attestation/api/jobs/{job['id']}/share", json={"days": 1})
    assert response.status_code == 403, "Link was created without the CSRF token"


def test_cannot_share_job_of_another_session(tmp_path):
    app, client, _ = make_service(tmp_path)
    log_in(client)
    job = finished(client)
    other = visitor(app)
    log_in(other)
    response = other.post(f"/attestation/api/jobs/{job['id']}/share", json={"days": 1}, headers=csrf(other))
    assert response.status_code == 404, "Another session shared someone else's report"


def test_cannot_share_for_longer_than_a_week(tmp_path):
    _, _, _, response = shared_job(tmp_path, days=30)
    assert response.status_code == 400, "Link was created for longer than 7 days"


def test_cannot_keep_token_on_disk(tmp_path):
    _, _, _, response = shared_job(tmp_path)
    stored = [path for path in (tmp_path / "data" / "shares").rglob("*")]
    names = " ".join(str(path) for path in stored)
    contents = b"".join(path.read_bytes() for path in stored if path.is_file())
    assert token_of(response) not in names and token_of(response).encode() not in contents, (
        "The token is stored on disk, so the files give the link away"
    )


def test_cannot_write_token_to_logs(tmp_path):
    configure_logging("angry-attestation-share", tmp_path / "logs")
    app, _, _, response = shared_job(tmp_path)
    open_share(app, token_of(response))
    logs = "".join(path.read_text("utf-8") for path in (tmp_path / "logs").glob("*.log"))
    assert token_of(response) not in logs, "The share token reached the logs"


def test_cannot_lose_shared_report_with_job_files(tmp_path):
    app, _, _, response = shared_job(tmp_path, files_ttl=-1)
    app.state.runner.cleanup()
    assert open_share(app, token_of(response)).status_code == 200, (
        "Shared report disappeared together with the job's 30-minute files"
    )


def test_cannot_keep_expired_share_on_disk(tmp_path, monkeypatch):
    app, _, _, response = shared_job(tmp_path, days=1)
    folder = tmp_path / "data" / "shares" / response.json()["id"]
    later = time.time() + 24 * 3600 + 1
    monkeypatch.setattr(shares_module.time, "time", lambda: later)
    app.state.shares.cleanup()
    assert not folder.exists(), "Student data of an expired link stayed on disk"


def test_cannot_keep_guessing_share_tokens(tmp_path):
    app, _, _, _ = shared_job(tmp_path)
    guesser = visitor(app)
    for _ in range(30):
        guesser.get("/attestation/api/shared", headers={"X-Share-Token": "x" * 43})
    assert guesser.get("/attestation/api/shared", headers={"X-Share-Token": "x" * 43}).status_code == 429, (
        "Wrong share tokens can be tried without limit"
    )


def test_cannot_open_shared_page_without_its_scripts(tmp_path):
    app, _, _, _ = shared_job(tmp_path)
    page = visitor(app).get("/attestation/share").text
    assert all(f"/attestation/static/{name}" in page for name in ("charts.js", "report.js", "share.js")), (
        "The shared page does not load its scripts"
    )


def test_cannot_serve_shared_page_without_security_policy(tmp_path):
    app, _, _, _ = shared_job(tmp_path)
    policy = visitor(app).get("/attestation/share").headers.get("content-security-policy", "")
    assert "script-src 'self'" in policy, "The shared page is served without its Content-Security-Policy"


def test_cannot_keep_half_written_share(tmp_path):
    store = ShareStore(tmp_path)
    draft = tmp_path / (shares_module.NEW_PREFIX + "0" * 64)
    draft.mkdir(parents=True)
    store.cleanup()
    assert not draft.exists(), "A draft left by a crash was not removed"
