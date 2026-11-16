"""What scheduled runs install and execute is pinned.

The scheduled and ChatOps workflows hold ESPN cookies, a Discord webhook and
``contents: write``. A moved action tag or a new dependency release would run
there unreviewed, so actions are pinned to commit SHAs and those four
workflows install against ``constraints.txt``. CI stays unconstrained, so a
new release is still tested before anyone bumps a pin.
"""
import re

from .conftest import REPO_ROOT as ROOT, requires_repo

WORKFLOWS = ROOT / ".github" / "workflows"
CONSTRAINED = ("weekly-report.yml", "waivers.yml", "values.yml", "chatops.yml")

#: Repository config, not package behaviour: see ``conftest.requires_repo``.
pytestmark = requires_repo


def _workflows():
    paths = sorted(WORKFLOWS.glob("*.yml"))
    # Never pass vacuously: with no files read, every check below holds.
    assert len(paths) >= 5, paths
    return paths


def test_every_action_is_pinned_to_a_commit_sha():
    bad = []
    for path in _workflows():
        for line in path.read_text().splitlines():
            m = re.search(r"uses:\s*(\S+)(.*)", line)
            if not m:
                continue
            ref, rest = m.groups()
            if not re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref) \
                    or not re.search(r"#\s*v\d+\.\d+\.\d+", rest):
                bad.append(f"{path.name}: {line.strip()}")
    assert bad == [], "pin to a full SHA with a '# vX.Y.Z' comment:\n" + "\n".join(bad)


def test_one_version_per_action():
    """The same action at two SHAs is a half-done bump."""
    seen = {}
    for path in _workflows():
        for ref in re.findall(r"uses:\s*(\S+)", path.read_text()):
            name, _, sha = ref.partition("@")
            seen.setdefault(name, set()).add(sha)
    assert {n: s for n, s in seen.items() if len(s) > 1} == {}


def test_scheduled_and_chatops_installs_use_the_constraints():
    for name in CONSTRAINED:
        installs = re.findall(r"pip install[^\n]*", (WORKFLOWS / name).read_text())
        assert installs, name
        assert all("-c constraints.txt" in line for line in installs), (name, installs)


def test_ci_installs_unconstrained():
    assert "constraints.txt" not in (WORKFLOWS / "ci.yml").read_text()


def test_constraints_pin_every_runtime_dependency_exactly():
    # A regex, not tomllib: CI still runs Python 3.10.
    block = re.search(r"^dependencies = \[(.*?)\]", (ROOT / "pyproject.toml").read_text(),
                      re.S | re.M)[1]
    deps = re.findall(r'"([^"]+)"', block)
    assert deps
    names = {re.split(r"[<>=!~\[ ;]", d, maxsplit=1)[0].lower() for d in deps}
    pins = {}
    for line in (ROOT / "constraints.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, _, version = line.partition("==")
            assert version, f"not an exact pin: {line}"
            pins[name.strip().lower()] = version.strip()
    assert names <= set(pins), names - set(pins)
