"""The one identity every outbound request carries.

Every source this app reads is a public page or a free endpoint, and all of them
serve a plain client: no browser, script, or login is needed. So requests say
what they are rather than borrowing a desktop browser's User-Agent, which two
scrapers used to do. Checked live 2026-11-02 against CBS, Yahoo, FantasyPros,
ESPN and Sleeper.

Don't add "compatible;" to this string: CBS answers 406 to that token
specifically, while every plain form of this string gets a 200.
"""

from __future__ import annotations

USER_AGENT = "ff-startsit/0.1 (+https://github.com/InvaderFry/FF-Weekly-Rankings)"
HEADERS = {"User-Agent": USER_AGENT}

#: Retries per request, after the first attempt. No request in this package
#: used to retry and nothing is cached between Actions runs, so one 502 at the
#: moment a scheduled run fetched cost that run the whole source.
RETRY_TOTAL = 3
#: urllib3's backoff factor: waits of about 0.5s, 1s, 2s between attempts.
RETRY_BACKOFF = 0.5
#: 429 and the transient 5xx family. 501 and 505 are not transient.
RETRY_STATUSES = (429, 500, 502, 503, 504)


def session():
    """A ``requests.Session`` that sends ``HEADERS`` and retries transient
    failures: connection errors, 429 and 5xx, for **GET and HEAD only**.

    Never POST. The one POST here is the Discord webhook, and a POST that
    reached Discord but failed on the way back would be posted twice.

    A server's ``Retry-After`` is ignored on purpose: a scheduled run would
    otherwise sleep as long as the server asks, and the backoff here stays a
    few seconds. When the retries run out, the last response is returned
    rather than raised, so each caller's own status handling still runs.
    Every ``session=`` parameter stays injectable, so tests never reach this.
    """
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retry = Retry(
        total=RETRY_TOTAL, connect=RETRY_TOTAL, read=RETRY_TOTAL,
        status=RETRY_TOTAL, backoff_factor=RETRY_BACKOFF,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False, respect_retry_after_header=False,
    )
    sess = requests.Session()
    sess.headers.update(HEADERS)
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess
