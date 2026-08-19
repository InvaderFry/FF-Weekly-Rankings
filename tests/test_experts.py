"""Finding and checking FantasyPros expert ids.

The two halves of this feature have deliberately different reliability, and the
tests hold each to its own standard:

* **Discovery** maps a name to an id off a per-expert page, and is markup-
  dependent. What must never happen is a *wrong* number — a wrong-but-valid id
  returns a real ranking, so the report would label some other analyst's numbers
  "Justin Boone" and nothing would look broken. So the parser is pinned to
  return ``None`` on anything it doesn't recognize.
* **Verification** parses nothing and compares rankings numerically, so it stays
  correct across layout changes. It is what catches the mislabel case above.
"""

from pathlib import Path

import pytest
import requests

from ff_startsit import cli
from ff_startsit.config import Settings
from ff_startsit.data.matching import ExternalRow
from ff_startsit.sources.experts import (EXPERT_PAGE_URL, ExpertFinder,
                                         expert_slug, format_env_line,
                                         parse_expert_directory,
                                         parse_expert_id, verify_experts)
from ff_startsit.sources.journalists import Expert

FIXTURES = Path(__file__).parent / "fixtures"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text()


class _FakeResp:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status


class _FakeSession:
    """Serves canned pages by URL; anything unlisted 404s."""

    def __init__(self, pages: dict, error: Exception = None):
        self.pages = pages
        self.error = error
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        if url not in self.pages:
            return _FakeResp("", status=404)
        return _FakeResp(self.pages[url])


def _page(name: str) -> str:
    return EXPERT_PAGE_URL.format(slug=expert_slug(name))


# --- slugs ----------------------------------------------------------------
def test_slug_is_the_analysts_name():
    assert expert_slug("Justin Boone") == "justin-boone"
    assert expert_slug("Jamey Eisenberg") == "jamey-eisenberg"


def test_slug_reuses_the_apps_one_name_normalization():
    """Reusing `normalize_name` keeps punctuation and suffix handling identical
    to the ECR join, rather than inventing a second convention that drifts."""
    assert expert_slug("D.J. O'Brien Jr.") == "dj obrien".replace(" ", "-")
    assert expert_slug("  Dave   Richard  ") == "dave-richard"


# --- id parsing -----------------------------------------------------------
@pytest.mark.parametrize("fixture,expected", [
    ("fantasypros_expert_page.html", "1234"),          # ?filters=NNNN link
    ("fantasypros_expert_page_alt.html", "120"),       # data-expert-id
    ("fantasypros_expert_page_json.html", "125"),      # embedded JSON
    ("fantasypros_expert_page_checkbox.html", "777"),  # picker checkbox
])
def test_each_supported_markup_shape_yields_the_id(fixture, expected):
    """The live markup couldn't be inspected when this was written, so the parser
    is a chain of candidate shapes rather than one pattern."""
    assert parse_expert_id(_fx(fixture)) == expected


def test_an_unrecognized_page_yields_none_not_a_stray_number():
    """The fixture contains "#1" and "42 experts" — a looser pattern would
    happily return one of those as an expert id, and a wrong id silently ships
    another analyst's ranks under your journalist's name."""
    assert parse_expert_id(_fx("fantasypros_no_id.html")) is None


def test_empty_input_is_safe():
    assert parse_expert_id("") is None
    assert parse_expert_id(None) is None


# --- directory ------------------------------------------------------------
def test_directory_lists_experts_and_drops_duplicates():
    experts = parse_expert_directory(_fx("fantasypros_experts_index.html"))
    assert [(e.id, e.name) for e in experts] == [
        ("1234", "Justin Boone"), ("120", "Jamey Eisenberg"), ("125", "Dave Richard")]


def test_directory_on_an_unrecognized_page_is_empty():
    assert parse_expert_directory(_fx("fantasypros_no_id.html")) == []


# --- the finder -----------------------------------------------------------
def test_find_resolves_a_name_to_an_id():
    session = _FakeSession({_page("Justin Boone"): _fx("fantasypros_expert_page.html")})
    found = ExpertFinder(session=session).find("Justin Boone")
    assert found == Expert(id="1234", name="Justin Boone")
    assert session.calls == [_page("Justin Boone")]


def test_find_all_reports_what_it_could_not_resolve():
    """A partial answer beats none: the ids that worked are still worth pasting."""
    session = _FakeSession({_page("Justin Boone"): _fx("fantasypros_expert_page.html")})
    found, missing = ExpertFinder(session=session).find_all(
        ["Justin Boone", "Dave Richard"])
    assert [e.id for e in found] == ["1234"]
    assert missing == ["Dave Richard"]


def test_a_404_warns_and_resolves_nothing(capsys):
    finder = ExpertFinder(session=_FakeSession({}))
    assert finder.find("Nobody Here") is None
    assert "HTTP 404" in capsys.readouterr().err


def test_a_network_error_warns_rather_than_raising(capsys):
    finder = ExpertFinder(session=_FakeSession({}, error=requests.RequestException("boom")))
    assert finder.find("Justin Boone") is None
    assert "couldn't reach" in capsys.readouterr().err


def test_a_page_without_an_id_says_so_specifically(capsys):
    """Distinct from a 404: we found their page, the markup just changed."""
    session = _FakeSession({_page("Dave Richard"): _fx("fantasypros_no_id.html")})
    assert ExpertFinder(session=session).find("Dave Richard") is None
    assert "no expert id on it" in capsys.readouterr().err


def test_lookups_are_memoized_including_failures():
    session = _FakeSession({})
    finder = ExpertFinder(session=session)
    for _ in range(3):
        finder.find("Justin Boone")
    assert len(session.calls) == 1


def test_env_line_is_paste_ready():
    line = format_env_line([Expert("1234", "Justin Boone"), Expert("120", "Jamey Eisenberg")])
    assert line == "FF_PREFERRED_EXPERTS=1234:Justin Boone,120:Jamey Eisenberg"
    # It must survive a round trip through the parser that reads it back.
    from ff_startsit.sources.journalists import parse_experts
    assert parse_experts(line.split("=", 1)[1]) == [
        Expert("1234", "Justin Boone"), Expert("120", "Jamey Eisenberg")]


# --- verification ---------------------------------------------------------
def _rows(names):
    return [ExternalRow(name=n, team="KC", position="RB", value=float(i + 1))
            for i, n in enumerate(names)]


CONSENSUS = ["Alpha Back", "Bravo Back", "Charlie Back"]


def _fetcher(by_filter: dict):
    """A stand-in for ecr.fetch_scrape_rows keyed on the `filters` argument."""
    def fetch(session, scoring, position, timeout=20, filters=None):
        return _rows(by_filter[filters])
    return fetch


def test_three_distinct_rankings_pass():
    checks = verify_experts(
        [Expert("1", "Boone"), Expert("2", "Eisenberg"), Expert("3", "Richard")],
        fetch=_fetcher({None: CONSENSUS,
                        "1": ["Bravo Back", "Alpha Back", "Charlie Back"],
                        "2": ["Charlie Back", "Alpha Back", "Bravo Back"],
                        "3": ["Alpha Back", "Charlie Back", "Bravo Back"]}))
    assert all(c.ok for c in checks)
    assert [c.rows for c in checks] == [3, 3, 3]


def test_an_id_returning_nothing_is_flagged():
    checks = verify_experts([Expert("9", "Ghost")],
                            fetch=_fetcher({None: CONSENSUS, "9": []}))
    assert not checks[0].ok
    assert "no rankings" in checks[0].problem


def test_an_id_matching_plain_consensus_is_flagged():
    """The failure the app could not catch before, and the dangerous one: a
    wrong-but-valid id returns real numbers, so the report renders somebody
    else's ranks under your journalist's name and nothing looks broken."""
    checks = verify_experts(
        [Expert("1", "Boone"), Expert("2", "Wrong Id")],
        fetch=_fetcher({None: CONSENSUS,
                        "1": ["Bravo Back", "Alpha Back", "Charlie Back"],
                        "2": CONSENSUS}))
    assert checks[0].ok
    assert not checks[1].ok
    assert "unfiltered consensus" in checks[1].problem


def test_experts_that_match_each_other_are_flagged():
    """FantasyPros ignoring the filter entirely — every request gets consensus."""
    same = ["Bravo Back", "Alpha Back", "Charlie Back"]
    checks = verify_experts(
        [Expert("1", "Boone"), Expert("2", "Eisenberg")],
        fetch=_fetcher({None: CONSENSUS, "1": same, "2": same}))
    assert not any(c.ok for c in checks)
    assert "ignored the filter" in checks[0].problem
    assert "2" in checks[0].problem and "1" in checks[1].problem


def test_a_failed_consensus_fetch_does_not_invent_a_problem(capsys):
    """No baseline means the consensus comparison is skipped, not failed —
    otherwise an offline run would condemn three perfectly good ids."""
    def fetch(session, scoring, position, timeout=20, filters=None):
        if filters is None:
            raise requests.RequestException("offline")
        return _rows(["Alpha Back"])
    checks = verify_experts([Expert("1", "Boone")], fetch=fetch)
    assert checks[0].ok


# --- the CLI --------------------------------------------------------------
class _Args:
    def __init__(self, names=(), list_all=False, verify=False):
        self.names = list(names)
        self.list_all = list_all
        self.verify = verify


def test_command_prints_the_env_line_and_exits_zero(capsys):
    session = _FakeSession({_page("Justin Boone"): _fx("fantasypros_expert_page.html")})
    rc = cli.cmd_experts(_Args(["Justin Boone"]), Settings(),
                         finder=ExpertFinder(session=session))
    assert rc == 0
    assert "FF_PREFERRED_EXPERTS=1234:Justin Boone" in capsys.readouterr().out


def test_a_partial_resolution_still_prints_what_it_found_but_exits_nonzero(capsys):
    session = _FakeSession({_page("Justin Boone"): _fx("fantasypros_expert_page.html")})
    rc = cli.cmd_experts(_Args(["Justin Boone", "Dave Richard"]), Settings(),
                         finder=ExpertFinder(session=session))
    captured = capsys.readouterr()
    assert rc == 1
    assert "1234:Justin Boone" in captured.out
    assert "Couldn't resolve: Dave Richard" in captured.err
    assert "Pick Experts" in captured.err       # the manual fallback


def test_no_names_explains_the_usage(capsys):
    assert cli.cmd_experts(_Args(), Settings()) == 1
    assert "Give at least one name" in capsys.readouterr().err


def test_verify_without_configuration_says_what_to_run_first(capsys):
    assert cli.cmd_experts(_Args(verify=True), Settings()) == 1
    assert "FF_PREFERRED_EXPERTS is not set" in capsys.readouterr().err


def test_verify_reports_success_and_is_honest_about_its_limits(capsys):
    from ff_startsit.sources.experts import ExpertCheck

    settings = Settings(preferred_experts="1:Boone,2:Eisenberg")
    rc = cli.cmd_experts(
        _Args(verify=True), settings,
        verifier=lambda experts, **kw: [ExpertCheck(e, rows=60) for e in experts])
    out = capsys.readouterr().out
    assert rc == 0
    assert "not that it belongs to the analyst you named it after" in out


def test_verify_exits_nonzero_when_an_id_looks_wrong(capsys):
    from ff_startsit.sources.experts import ExpertCheck

    settings = Settings(preferred_experts="1:Boone")
    rc = cli.cmd_experts(
        _Args(verify=True), settings,
        verifier=lambda experts, **kw: [ExpertCheck(experts[0], 0, "returned no rankings")])
    assert rc == 1
    assert "At least one id looks wrong" in capsys.readouterr().err


def test_list_dumps_the_directory(capsys):
    session = _FakeSession(
        {"https://www.fantasypros.com/experts/nfl/": _fx("fantasypros_experts_index.html")})
    rc = cli.cmd_experts(_Args(list_all=True), Settings(),
                         finder=ExpertFinder(session=session))
    assert rc == 0
    assert "3 experts" in capsys.readouterr().out


def test_the_command_reads_no_roster_and_writes_no_results_log(tmp_path, monkeypatch):
    """A setup helper has no business touching a league or the #7 corpus."""
    def _boom(*a, **k):
        raise AssertionError("cmd_experts must not build a roster provider")

    monkeypatch.setattr(cli, "build_roster_provider", _boom)
    monkeypatch.setattr(cli, "_get_roster", _boom)
    settings = Settings(data_dir=tmp_path)
    session = _FakeSession({_page("Justin Boone"): _fx("fantasypros_expert_page.html")})
    cli.cmd_experts(_Args(["Justin Boone"]), settings,
                    finder=ExpertFinder(session=session))
    assert not settings.results_log_path.exists()
