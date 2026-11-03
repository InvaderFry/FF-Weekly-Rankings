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
