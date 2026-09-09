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


def verify_site(directory):
    missing = [name for name in ("index.html", "waivers.html")
               if not (directory / name).is_file() or not (directory / name).stat().st_size]
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
    """Use the requested season/week, retaining legacy issues as history."""
    import re
    match = re.search(r"Season (\d{4}) · Week (\d+)", report)
    if not match:
        raise ValueError("Report has no season/week identity; refusing an ambiguous issue")
    return f"{match[1]} Week {match[2]} {kind}"


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


if __name__ == "__main__":
    main()
