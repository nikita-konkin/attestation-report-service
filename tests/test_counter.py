from fastapi.testclient import TestClient
from helpers import log_in, make_service

COUNTER = 'data-goatcounter="https://volgatech-itrc.goatcounter.com/count"'
SCRIPT = 'src="/attestation/static/count.js"'


def test_cannot_open_login_page_without_counting_it(tmp_path):
    _, client, _ = make_service(tmp_path)
    page = client.get("/attestation").text
    assert COUNTER in page and SCRIPT in page, "The login page is not counted"


def test_cannot_open_report_page_without_counting_it(tmp_path):
    _, client, _ = make_service(tmp_path)
    log_in(client)
    page = client.get("/attestation").text
    assert COUNTER in page and SCRIPT in page, "The report page is not counted"


def test_cannot_open_shared_page_without_counting_it(tmp_path):
    app, _, _ = make_service(tmp_path)
    page = TestClient(app, base_url="https://testserver").get("/attestation/share").text
    assert COUNTER in page and SCRIPT in page, "The shared page is not counted"


def test_cannot_send_shared_report_title_to_counter(tmp_path):
    app, _, _ = make_service(tmp_path)
    page = TestClient(app, base_url="https://testserver").get("/attestation/share").text
    assert "data-goatcounter-settings='{\"title\": \"Результаты аттестации\"}'" in page, (
        "The counter would receive the shared report's own title"
    )


def test_cannot_block_counter_script(tmp_path):
    _, client, _ = make_service(tmp_path)
    assert client.get("/attestation/static/count.js").status_code == 200, "Counter script is not served"


def test_cannot_block_counter_beacon(tmp_path):
    _, client, _ = make_service(tmp_path)
    assert "connect-src 'self' https://volgatech-itrc.goatcounter.com;" in client.get("/attestation").headers[
        "content-security-policy"
    ], "The policy stops the counter's beacon"
