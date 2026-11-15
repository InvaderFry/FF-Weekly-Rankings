"""One session factory retries transient failures; nothing builds its own.

No request in the package retried, and nothing is cached between Actions runs,
so a single 502 from FantasyPros or ESPN at the moment a scheduled run fetched
cost that run the source. ``net.session()`` mounts a short urllib3 ``Retry``
for GET/HEAD on 429 and 5xx. The tests use a loopback server, so they stay
offline.
"""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from ff_startsit import net

PACKAGE = Path(net.__file__).resolve().parent


class _Flaky(BaseHTTPRequestHandler):
    """Fails the first ``fail`` requests with ``status``, then answers 200."""
    calls = 0
    fail = 0
    status = 502

    def _answer(self):
        type(self).calls += 1
        if type(self).calls <= type(self).fail:
            self.send_response(type(self).status)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(self.headers.get("User-Agent", "").encode())

    do_GET = _answer
    do_POST = _answer

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    handler = type("Handler", (_Flaky,), {"calls": 0, "fail": 0, "status": 502})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01},
                              daemon=True)
    thread.start()
    yield handler, f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(net, "RETRY_BACKOFF", 0.0)
    # Loopback only: never route these through a developer's HTTP proxy.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_transient_get_failure_is_retried(server, status):
    handler, url = server
    handler.fail, handler.status = 2, status
    resp = net.session().get(url, timeout=5)
    assert resp.status_code == 200
    assert handler.calls == 3


def test_retries_give_up_and_return_the_last_response(server):
    """Exhausted retries hand back the final response rather than raising,
    so each caller's own status handling (e.g. ESPN's RosterError naming the
    status) still runs."""
    handler, url = server
    handler.fail = 99
    resp = net.session().get(url, timeout=5)
    assert resp.status_code == 502
    assert handler.calls == 1 + net.RETRY_TOTAL


def test_a_client_error_is_not_retried(server):
    handler, url = server
    handler.fail, handler.status = 1, 404
    assert net.session().get(url, timeout=5).status_code == 404
    assert handler.calls == 1


def test_a_post_is_never_retried(server):
    """The Discord webhook is a POST: retrying one that reached Discord but
    failed on the way back posts the message twice."""
    handler, url = server
    handler.fail = 1
    assert net.session().post(url, json={}, timeout=5).status_code == 502
    assert handler.calls == 1


def test_the_session_sends_the_tool_identity(server):
    _, url = server
    assert net.session().get(url, timeout=5).text == net.USER_AGENT


def test_no_module_builds_a_bare_session():
    """Every ``session=`` default goes through ``net.session()``."""
    offenders = [str(p.relative_to(PACKAGE)) for p in PACKAGE.rglob("*.py")
                 if p.name != "net.py"
                 and "requests.Session()" in p.read_text(encoding="utf-8")]
    assert offenders == [], f"use ff_startsit.net.session() instead: {offenders}"
