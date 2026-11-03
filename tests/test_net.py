"""Every outbound request says what it is.

Two scrapers used to send a desktop-Chrome User-Agent. Every source this app
reads serves a plain client, so impersonating a browser bought nothing and
misrepresented the tool. These guards keep that from creeping back.
"""
from pathlib import Path

from ff_startsit import net

PACKAGE = Path(net.__file__).resolve().parent


def test_user_agent_names_the_tool_not_a_browser():
    ua = net.USER_AGENT
    assert ua.startswith("ff-startsit/")
    for browser in ("Mozilla", "Chrome", "Safari", "AppleWebKit", "Gecko"):
        assert browser not in ua
    # CBS answers 406 to this token specifically (see net.py).
    assert "compatible" not in ua


def test_no_module_hardcodes_its_own_user_agent():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if path.name == "net.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "User-Agent" in text or "Mozilla/" in text:
            offenders.append(str(path.relative_to(PACKAGE)))
    assert offenders == [], f"use ff_startsit.net.HEADERS instead: {offenders}"
