"""Testable entry points used directly by GitHub Actions."""
from pathlib import Path
import os
import sys


def chatops_reply(comment):
    import contextlib
    import io
    from ff_startsit.chatops import parse_command
    from ff_startsit.cli import main

    argv = parse_command(comment)
    if not argv:
        return None  # not a recognized command -> stay silent

    buf = io.StringIO()
    code = 0
    err = None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = main(argv) or 0
    except SystemExit as exc:
        # argparse raises SystemExit (a BaseException) on a usage error, so
        # `except Exception` missed it entirely: reply.md was never written
        # and the reply step's hashFiles guard then suppressed any comment.
        # The user got silence plus a red run.
        code = exc.code if isinstance(exc.code, int) else 2
    except Exception as exc:  # noqa: BLE001 - surface any failure to the comment
        err = str(exc)

    text = buf.getvalue().strip()
    if err is not None:
        out = f"⚠️ Error running `{' '.join(argv)}`:\n\n```\n{err}\n```"
    elif code != 0:
        # A nonzero exit with only stderr output used to post "_(no output)_".
        detail = text or f"exited with status {code}"
        out = f"⚠️ `{' '.join(argv)}` failed:\n\n```\n{detail}\n```"
    else:
        out = text or "_(no output)_"

    return f"> `{' '.join(argv)}`\n\n{out}\n"


#: Every page the Pages site serves. A deploy replaces the whole site, so a
#: workflow that builds one of these must carry the others.
SITE_PAGES = ("index.html", "waivers.html", "values.html")
#: Pages a deploy can never go without. values.html joined later and is
#: restored rather than required: before its first Wednesday it doesn't exist.
REQUIRED_PAGES = ("index.html", "waivers.html")
#: Left in the site directory when a page couldn't be restored. Read by
#: ``verify_site`` as a missing page, so the deploy is skipped.
RESTORE_FAILED = ".restore-failed-"


def restore_pages(base_url, directory, pages, session=None, optional=False):
    """Copy the *published* pages this run didn't build into ``directory``.

    The alternative, re-scoring every page in every workflow, costs Odds API
    credits for pages nobody asked to refresh. Fetching the live copy keeps the
    deploy complete for free. Three outcomes per page:

    * ``restored`` — the live copy is now in the directory;
    * ``absent`` — the site answered 404: never published, nothing to keep;
    * ``failed`` — anything else. The page exists or might, and deploying
      without it would delete it, so a marker file makes ``verify_site`` skip
      the deploy and leave the live site standing.

    A page already in ``directory`` was built by this run and is left alone.

    ``optional`` drops the marker: the page is simply left out of this deploy.
    The start/sit and waiver workflows restore values.html that way, because a
    Thursday lineup page that fails to deploy costs more than a values page
    missing until the next Wednesday rebuilds it.
    """
    import time
    import requests
    from .net import HEADERS
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()
    outcome = {}
    for page in pages:
        target = directory / page
        if target.is_file() and target.stat().st_size:
            outcome[page] = "built"
            continue
        url = f"{base_url.rstrip('/')}/{page}?v={int(time.time())}"   # past the CDN cache
        try:
            resp = session.get(url, headers=HEADERS, timeout=30)
            status = resp.status_code
        except Exception:
            status = None
        if status == 200 and resp.text.strip():
            target.write_text(resp.text, encoding="utf-8")
            outcome[page] = "restored"
        elif status == 404:
            outcome[page] = "absent"
        elif optional:
            outcome[page] = "skipped"
            print(f"::warning::{page} could not be restored (HTTP {status}); "
                  "this deploy leaves it out until its own workflow rebuilds it.")
        else:
            (directory / f"{RESTORE_FAILED}{page}").write_text(str(status))
            outcome[page] = "failed"
        print(f"{page}: {outcome[page]}")
    return outcome


def verify_site(directory):
    directory = Path(directory)
    missing = [name for name in REQUIRED_PAGES
               if not (directory / name).is_file() or not (directory / name).stat().st_size]
    missing += [m.name[len(RESTORE_FAILED):] for m in sorted(directory.glob(RESTORE_FAILED + "*"))]
    complete = not missing
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"complete={str(complete).lower()}\n")
    if missing:
        message = "Pages deploy skipped: missing " + ", ".join(missing) + ". Previous site preserved and stale."
        print("::warning::" + message)
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("\n" + message + "\n")
    return complete


def run_cli(argv):
    """Mirror existing diagnostics into Actions, redacting configured credentials."""
    import contextlib
    import io
    from .cli import main as cli_main

    def redact(text):
        for name, value in os.environ.items():
            if value and len(value) >= 4 and any(word in name for word in
                    ("TOKEN", "SECRET", "KEY", "WEBHOOK", "ESPN_", "FF_LEAGUES")):
                text = text.replace(value, "[redacted]")
        return text

    if os.environ.get("ARTIFACT_ONLY") == "true":
        argv = [arg for arg in argv if arg not in ("--discord", "--log")]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            return cli_main(argv) or 0
    finally:
        detail = redact(buf.getvalue())
        sys.stderr.write(detail)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if detail and summary_path:
            with open(summary_path, "a") as summary:
                summary.write("\n### Data and publishing diagnostics\n\n")
                summary.write("\n".join("> " + line for line in detail.splitlines()) + "\n")


def issue_title(report, kind):
    """Use the requested season/week, retaining legacy issues as history.

    Reads the identity out of the rendered Data status line rather than being
    told it, so the title can never disagree with the report it labels. The
    separator is matched loosely for that reason — a renderer is free to change
    how it punctuates that line, and the workflows must not need editing in
    step. `tests/test_workflow.py` pins the coupling from the other end, so a
    renderer that drops the line fails a test rather than a Wednesday run.

    Refusing is deliberate: a title guessed from a report whose week cannot be
    read is how a digest lands in the wrong week's issue. The callers make it
    survivable — the issue step warns and continues.
    """
    import re
    match = re.search(r"Season (\d{4})[^\d\n]{1,5}Week (\d+)", report)
    if not match:
        raise ValueError("Report has no season/week identity; refusing an ambiguous issue")
    return f"{match[1]} Week {match[2]} {kind}"


def publish_news(report_path, previous_path, output=None):
    """Whether a scheduled waiver run should publish (issue, Pages, Discord).

    The decision is ``waivers.board.report_has_news``; this only reads the two
    files and writes ``news=true|false`` to ``$GITHUB_OUTPUT``. A missing
    previous file means nothing was found to compare against.
    """
    from .waivers.board import report_has_news
    previous = Path(previous_path)
    news = report_has_news(Path(report_path).read_text(encoding="utf-8"),
                           previous.read_text(encoding="utf-8") if previous.exists() else "")
    line = f"news={'true' if news else 'false'}"
    print(line)
    output = output or os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as handle:
            handle.write(line + "\n")
    return news


def main():
    if sys.argv[1] == "chatops":
        reply = chatops_reply(os.environ.get("COMMENT_BODY", ""))
        if reply is not None:
            Path("reply.md").write_text(reply, encoding="utf-8")
    elif sys.argv[1] == "identity":
        print(issue_title(Path(sys.argv[2]).read_text(), sys.argv[3]))
    elif sys.argv[1] == "run":
        raise SystemExit(run_cli(sys.argv[2:]))
    elif sys.argv[1] == "site":
        verify_site(Path("site"))
    elif sys.argv[1] == "news":
        publish_news(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "restore":
        args = sys.argv[3:]
        optional = "--optional" in args
        restore_pages(sys.argv[2], Path("site"), [a for a in args if a != "--optional"],
                      optional=optional)


if __name__ == "__main__":
    main()
