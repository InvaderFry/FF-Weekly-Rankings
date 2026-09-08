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
from ff_startsit.sources.experts import (EXPERT_PAGE_URL, RANKINGS_PAGE_URL,
                                         ExpertFinder,
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


def _directory_session(extra: dict = None) -> _FakeSession:
    """A session serving the rankings page that carries the expert directory."""
    pages = {RANKINGS_PAGE_URL: _fx("fantasypros_rankings_directory.html")}
    pages.update(extra or {})
    return _FakeSession(pages)


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
    """One request to the rankings page answers the whole question."""
    session = _directory_session()
    found = ExpertFinder(session=session).find("Justin Boone")
    assert found == Expert(id="317", name="Justin Boone")
    assert session.calls == [RANKINGS_PAGE_URL]


def test_find_falls_back_to_the_per_expert_page_without_a_directory():
    """A directory we cannot read must not become a directory saying "no"."""
    session = _FakeSession({_page("Justin Boone"): _fx("fantasypros_expert_page.html")})
    found = ExpertFinder(session=session).find("Justin Boone")
    assert found == Expert(id="1234", name="Justin Boone")
    assert session.calls == [RANKINGS_PAGE_URL, _page("Justin Boone")]


def test_a_name_absent_from_a_readable_directory_is_not_retried_elsewhere():
    """Settled, not merely unresolved.

    If the rankings page lists every expert it can serve and this analyst is not
    among them, no id exists that would work — so we say which question was
    actually answered, and we don't spend a request on their profile page.
    """
    session = _directory_session()
    finder = ExpertFinder(session=session)
    assert finder.find("Jamey Eisenberg") is None
    assert session.calls == [RANKINGS_PAGE_URL]
    assert "3 experts" in finder.notes["Jamey Eisenberg"]


def test_one_directory_fetch_serves_every_name():
    session = _directory_session()
    finder = ExpertFinder(session=session)
    found, missing = finder.find_all(["Justin Boone", "Andy Behrens", "Nobody Here"])
    assert [(e.id, e.name) for e in found] == [("317", "Justin Boone"),
                                               ("9", "Andy Behrens")]
    assert missing == ["Nobody Here"]
    assert session.calls == [RANKINGS_PAGE_URL]


def test_the_directorys_spelling_of_a_name_wins():
    """The report's byline and FantasyPros' byline stay the same string."""
    found = ExpertFinder(session=_directory_session()).find("justin  boone")
    assert found == Expert(id="317", name="Justin Boone")


def test_lookup_id_names_whose_id_it_is():
    """The only check that can catch a mislabel — see verify_experts."""
    finder = ExpertFinder(session=_directory_session())
    assert finder.lookup_id("317") == Expert(id="317", name="Justin Boone")
    assert finder.lookup_id("44") is None


def test_directory_distinguishes_unreadable_from_empty():
    """``None`` means "we could not look", never "nobody is listed"."""
    assert ExpertFinder(session=_FakeSession({})).directory() is None


def test_directory_survives_brackets_inside_the_json():
    """A regex would stop at the first ``]`` inside an image URL; this doesn't."""
    experts = parse_expert_directory(_fx("fantasypros_rankings_directory.html"))
    assert [e.id for e in experts] == ["317", "9", "4317"]


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
    # One directory attempt plus one per-expert-page fallback, then nothing:
    # both the directory read and the failed lookup are cached.
    assert session.calls == [RANKINGS_PAGE_URL, _page("Justin Boone")]


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


DIRECTORY = [Expert("1", "Boone"), Expert("2", "Eisenberg"), Expert("3", "Richard")]


def test_an_id_absent_from_the_directory_is_called_dead_not_misread():
    """The fix that matters most: "wrong id" and "dead id" need different actions."""
    checks = verify_experts([Expert("44", "Dave Richard")],
                            fetch=_fetcher({None: CONSENSUS, "44": []}),
                            directory=DIRECTORY)
    assert not checks[0].ok
    assert "dead" in checks[0].problem and "3 weekly rankers" in checks[0].problem


def test_an_id_belonging_to_another_analyst_is_flagged_before_any_fetch():
    """A mislabel returns real numbers, so no ranking comparison can reveal it.

    Only the directory can, and it does so without spending a request — the
    ranks would be perfectly valid, just filed under the wrong person.
    """
    def _explode(*a, **k):
        raise AssertionError("a mislabel is settled without fetching ranks")

    checks = verify_experts([Expert("1", "Dave Richard")],
                            fetch=lambda s, sc, pos, timeout=20, filters=None:
                                _rows(CONSENSUS) if filters is None else _explode(),
                            directory=DIRECTORY)
    assert not checks[0].ok
    assert "is Boone, not Dave Richard" in checks[0].problem
    assert checks[0].actual_name == "Boone"


def test_a_vouched_id_returning_nothing_blames_the_transport_not_the_id():
    """The misdiagnosis this replaces.

    The public page filters in the browser, so a perfectly good id returns
    nothing through the scrape. Reporting "the id is wrong" sent the user off to
    re-derive an id that was already correct.
    """
    checks = verify_experts([Expert("1", "Boone")],
                            fetch=_fetcher({None: CONSENSUS, "1": []}),
                            directory=DIRECTORY)
    assert not checks[0].ok
    assert "the id is valid" in checks[0].problem
    assert "FANTASYPROS_API_KEY" in checks[0].problem


def test_without_a_directory_the_old_diagnosis_still_stands():
    """No directory downgrades the verdict; it never invents a stronger one."""
    checks = verify_experts([Expert("9", "Ghost")],
                            fetch=_fetcher({None: CONSENSUS, "9": []}))
    assert "the id is wrong or has no weekly data yet" in checks[0].problem


def test_a_directory_name_spelled_differently_is_not_a_mislabel():
    """"J.J. Zachariason" vs "JJ Zachariason" is the same person."""
    checks = verify_experts([Expert("1", "boone")],
                            fetch=_fetcher({None: CONSENSUS, "1": ["Alpha Back"]}),
                            directory=DIRECTORY)
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


def test_verify_hands_the_directory_to_the_verifier():
    """cmd_experts owns the lookup; verification stays offline-injectable."""
    seen = {}

    def _verifier(experts, **kw):
        seen.update(kw)
        return []

    cli.cmd_experts(_Args(verify=True), Settings(preferred_experts="317:Justin Boone"),
                    finder=ExpertFinder(session=_directory_session()),
                    verifier=_verifier)
    assert [(e.id, e.name) for e in seen["directory"]] == [
        ("317", "Justin Boone"), ("9", "Andy Behrens"), ("4317", "Pat Fitzmaurice")]


def test_an_unlisted_analyst_is_not_sent_to_the_manual_lookup(capsys):
    """The manual steps find an id. There is no id to find, so don't offer them."""
    rc = cli.cmd_experts(_Args(["Jamey Eisenberg"]), Settings(),
                         finder=ExpertFinder(session=_directory_session()))
    err = capsys.readouterr().err
    assert rc == 1
    assert "Pick Experts" not in err
    assert "doesn't publish weekly NFL ranks" in err
    assert "columns are scraped separately" in err  # and still work


def test_a_mix_of_unlisted_and_unreachable_still_offers_the_manual_steps(capsys):
    """One name we genuinely couldn't look up is enough to keep the fallback.

    Driven through a stub finder rather than a session: with a readable
    directory every miss is settled, so the mixed state this guard exists for
    can't be produced by a real lookup — which is exactly why the guard is a
    check on ``unlisted`` and not on "did anything fail".
    """
    class _StubFinder:
        pages: dict = {}
        notes = {"Jamey Eisenberg": "not one of the 3 experts",
                 "Someone Else": "their FantasyPros page could not be fetched"}
        unlisted = {"Jamey Eisenberg"}

        def find_all(self, names):
            return [], list(names)

    cli.cmd_experts(_Args(["Jamey Eisenberg", "Someone Else"]), Settings(),
                    finder=_StubFinder())
    err = capsys.readouterr().err
    assert "Pick Experts" in err          # still reachable for the unknown one
    assert "Jamey Eisenberg: not one of the 3 experts" in err


def test_a_valid_id_that_returns_nothing_is_not_sent_to_the_manual_lookup(capsys):
    """The remedy has to match the diagnosis.

    Re-deriving an id the directory just vouched for returns the same number.
    What the user actually needs is the API key.
    """
    from ff_startsit.sources.experts import ExpertCheck

    rc = cli.cmd_experts(
        _Args(verify=True), Settings(preferred_experts="317:Justin Boone"),
        finder=ExpertFinder(session=_directory_session()),
        verifier=lambda experts, **kw: [
            ExpertCheck(experts[0], 0, "no ranks came back, but the id is valid",
                        id_ok=True)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "Pick Experts" not in err
    assert "FANTASYPROS_API_KEY" in err


def test_a_dead_id_still_gets_the_manual_lookup(capsys):
    from ff_startsit.sources.experts import ExpertCheck

    cli.cmd_experts(
        _Args(verify=True), Settings(preferred_experts="44:Dave Richard"),
        finder=ExpertFinder(session=_directory_session()),
        verifier=lambda experts, **kw: [
            ExpertCheck(experts[0], 0, "this id is dead", id_ok=False)])
    assert "Pick Experts" in capsys.readouterr().err
