"""Links to report results that open without a login."""

import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from helpers import RITM_PASSWORD, csrf, finished, log_in, make_service

from app import shares as shares_module
from app.logging_config import configure_logging
from app.shares import ShareStore


def shared_job(tmp_path, days: int | None = 3, **overrides):
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


def test_cannot_lose_link_address_from_owner_list(tmp_path):
    app, client, _, response = shared_job(tmp_path)
    link = client.get("/attestation/api/shares").json()["shares"][0]["link"]
    assert open_share(app, link.split("#", 1)[1]).status_code == 200, "The owner's list does not give the link again"


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


def test_cannot_name_share_folder_by_token(tmp_path):
    _, _, _, response = shared_job(tmp_path)
    names = " ".join(str(path) for path in (tmp_path / "data" / "shares").rglob("*"))
    assert token_of(response) not in names, "A file name gives the link away"


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


def later_than_a_week(monkeypatch):
    """A year on for the share store only: the test client's cookie must not expire with it."""
    later = time.time() + 365 * 24 * 3600
    monkeypatch.setattr(shares_module, "time", SimpleNamespace(time=lambda: later))


def test_cannot_expire_permanent_link(tmp_path, monkeypatch):
    app, _, _, response = shared_job(tmp_path, days=None)
    later_than_a_week(monkeypatch)
    assert open_share(app, token_of(response)).status_code == 200, "A permanent link expired"


def test_cannot_delete_permanent_link_on_cleanup(tmp_path, monkeypatch):
    app, _, _, response = shared_job(tmp_path, days=None)
    later_than_a_week(monkeypatch)
    app.state.shares.cleanup()
    assert (tmp_path / "data" / "shares" / response.json()["id"]).exists(), "Cleanup deleted a permanent link"


def test_cannot_keep_expiry_after_making_link_permanent(tmp_path, monkeypatch):
    app, client, _, response = shared_job(tmp_path, days=1)
    client.post(f"/attestation/api/shares/{response.json()['id']}/permanent", headers=csrf(client))
    later_than_a_week(monkeypatch)
    assert open_share(app, token_of(response)).status_code == 200, "A link made permanent still expired"


def test_cannot_show_expiry_of_permanent_link_in_list(tmp_path):
    _, client, _, response = shared_job(tmp_path, days=3)
    client.post(f"/attestation/api/shares/{response.json()['id']}/permanent", headers=csrf(client))
    assert client.get("/attestation/api/shares").json()["shares"][0]["expires"] is None, (
        "The owner's list still shows an expiry for a permanent link"
    )


def test_cannot_make_link_permanent_without_csrf_token(tmp_path):
    _, client, _, response = shared_job(tmp_path, days=1)
    answer = client.post(f"/attestation/api/shares/{response.json()['id']}/permanent")
    assert answer.status_code == 403, "A link was made permanent without the CSRF token"


def test_cannot_make_link_of_another_teacher_permanent(tmp_path):
    app, _, _, response = shared_job(tmp_path, days=1)
    other = visitor(app)
    other.post("/attestation/login", data={"username": "marstu.net\\other", "password": RITM_PASSWORD})
    other.post(f"/attestation/api/shares/{response.json()['id']}/permanent", headers=csrf(other))
    assert app.state.shares.get(token_of(response)).expires is not None, (
        "Another teacher made someone else's link permanent"
    )


def test_cannot_revive_expired_link(tmp_path, monkeypatch):
    app, client, _, response = shared_job(tmp_path, days=1)
    later_than_a_week(monkeypatch)
    answer = client.post(f"/attestation/api/shares/{response.json()['id']}/permanent", headers=csrf(client))
    assert answer.status_code == 404, "An expired link was brought back as permanent"


def test_cannot_keep_permanent_link_after_revoke(tmp_path):
    app, client, _, response = shared_job(tmp_path, days=None)
    client.delete(f"/attestation/api/shares/{response.json()['id']}", headers=csrf(client))
    assert open_share(app, token_of(response)).status_code == 404, "A permanent link still opened after revoke"


def test_cannot_offer_permanent_lifetime_wrongly(tmp_path):
    _, client, _, _ = shared_job(tmp_path)
    assert client.get("/attestation/api/shares").json()["lifetimes"] == [1, 3, 7, None], (
        "The page is not offered 1, 3, 7 days and a permanent link"
    )


def other_teacher(app) -> TestClient:
    other = visitor(app)
    other.post("/attestation/login", data={"username": "marstu.net\\other", "password": RITM_PASSWORD})
    return other


def test_cannot_open_own_shared_report_on_service_page(tmp_path):
    _, client, job, response = shared_job(tmp_path)
    report = client.get(f"/attestation/api/shares/{response.json()['id']}/report").json()
    assert report["markdown"] == job["markdown"], "The owner cannot open the report of their link again"


def test_cannot_download_own_shared_file_on_service_page(tmp_path):
    _, client, _, response = shared_job(tmp_path, files_ttl=-1)
    report = client.get(f"/attestation/api/shares/{response.json()['id']}/report").json()
    assert client.get(report["downloads"][0]["files"][0]["href"]).content == b"docx", (
        "The owner cannot download the files of their link"
    )


def test_cannot_open_shared_report_of_another_teacher_by_id(tmp_path):
    app, _, _, response = shared_job(tmp_path)
    answer = other_teacher(app).get(f"/attestation/api/shares/{response.json()['id']}/report")
    assert answer.status_code == 404, "Another teacher opened someone else's shared report by its id"


def test_cannot_download_shared_file_of_another_teacher_by_id(tmp_path):
    app, client, _, response = shared_job(tmp_path)
    href = client.get(f"/attestation/api/shares/{response.json()['id']}/report").json()["downloads"][0]["files"][0]["href"]
    assert other_teacher(app).get(href).status_code == 404, "Another teacher downloaded someone else's shared file"


def test_cannot_open_shared_report_by_id_without_login(tmp_path):
    app, _, _, response = shared_job(tmp_path)
    answer = visitor(app).get(f"/attestation/api/shares/{response.json()['id']}/report")
    assert answer.status_code == 401, "A shared report opened by its id without a login"


def reissue(client, response):
    return client.post(f"/attestation/api/shares/{response.json()['id']}/reissue", headers=csrf(client))


def test_cannot_open_old_link_after_reissue(tmp_path):
    app, client, _, response = shared_job(tmp_path)
    reissue(client, response)
    assert open_share(app, token_of(response)).status_code == 404, "The old link still opens after a new one was issued"


def test_cannot_open_new_link_after_reissue(tmp_path):
    app, client, _, response = shared_job(tmp_path)
    fresh = reissue(client, response)
    assert open_share(app, token_of(fresh)).status_code == 200, "The reissued link does not open"


def test_cannot_change_lifetime_on_reissue(tmp_path):
    _, client, _, response = shared_job(tmp_path, days=3)
    assert reissue(client, response).json()["expires"] == response.json()["expires"], (
        "Reissuing changed how long the link opens"
    )


def test_cannot_reissue_link_of_another_teacher(tmp_path):
    app, _, _, response = shared_job(tmp_path)
    other = other_teacher(app)
    other.post(f"/attestation/api/shares/{response.json()['id']}/reissue", headers=csrf(other))
    assert open_share(app, token_of(response)).status_code == 200, "Another teacher replaced someone else's link"


def test_cannot_reissue_link_without_csrf_token(tmp_path):
    _, client, _, response = shared_job(tmp_path)
    answer = client.post(f"/attestation/api/shares/{response.json()['id']}/reissue")
    assert answer.status_code == 403, "A link was replaced without the CSRF token"


def forget_token(tmp_path, response) -> None:
    """A link made before tokens were kept in share.json."""
    meta = tmp_path / "data" / "shares" / response.json()["id"] / shares_module.META_FILE
    data = json.loads(meta.read_text(encoding="utf-8"))
    del data["token"]
    meta.write_text(json.dumps(data), encoding="utf-8")


def test_cannot_break_old_link_without_kept_token(tmp_path):
    app, client, _, response = shared_job(tmp_path)
    forget_token(tmp_path, response)
    assert client.get("/attestation/api/shares").json()["shares"][0]["link"] is None and (
        open_share(app, token_of(response)).status_code == 200
    ), "A link made before tokens were kept broke or got a wrong address"


def test_cannot_reissue_old_link_without_kept_token(tmp_path):
    app, client, _, response = shared_job(tmp_path)
    forget_token(tmp_path, response)
    link = reissue(client, response).json()["link"]
    assert open_share(app, link.split("#", 1)[1]).status_code == 200, "An old link without an address was not reissued"
