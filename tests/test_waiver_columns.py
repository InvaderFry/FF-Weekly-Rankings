"""The CBS/Yahoo waiver-column scrapes: decorative, and safe when they fail.

The extraction is inverted on purpose — it searches the article for names that
are already in the free-agent pool, rather than parsing the article's structure.
So the tests pin two things: a real name in the prose is found, and nothing that
isn't a pool player can ever get through.
"""

from pathlib import Path

import requests

from ff_startsit.models import Player
from ff_startsit.waivers.columns import (ColumnFetcher, ColumnSource,
                                         extract_mentions, find_column_url,
                                         index_mentions, strip_tags)

FIXTURES = Path(__file__).parent / "fixtures"
INDEX = (FIXTURES / "cbs_author_index.html").read_text()
COLUMN = (FIXTURES / "cbs_waiver_column.html").read_text()
PAYWALL = (FIXTURES / "cbs_paywall.html").read_text()

POOL = [
    Player("espn-1", "Jauan Jennings", "SF", "WR"),
    Player("espn-2", "Zach Charbonnet", "SEA", "RB"),
    Player("espn-3", "DJ Moore", "CHI", "WR"),
    Player("espn-4", "Never Mentioned", "NYJ", "RB"),
    Player("KC", "Kansas City", "KC", "DEF"),
]

SOURCE = ColumnSource("Dave Richard", "https://cbs.test/writers/dave-richard/",
                      "https://cbs.test")


class _FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class _FakeSession:
    def __init__(self, by_url):
        self.by_url = by_url
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        value = self.by_url.get(url)
        if isinstance(value, Exception):
            raise value
        if value is None:
            return _FakeResp("", status=404)
        return _FakeResp(value)


# --- pure helpers ---------------------------------------------------------
def test_the_week_specific_column_wins_over_an_older_one():
    url = find_column_url(INDEX, "https://www.cbssports.com", 9)
    assert url.endswith("fantasy-football-week-9-waiver-wire-adds/")


def test_an_unnamed_week_falls_back_to_the_most_recent_waiver_link():
    """Author pages list newest first, and not every slug carries the week."""
    url = find_column_url(INDEX, "https://www.cbssports.com", 12)
    assert "waiver" in url


def test_a_page_with_no_waiver_link_returns_none():
    assert find_column_url("<a href='/news/other/'>x</a>", "https://x.test", 9) is None


def test_only_pool_players_are_extracted():
    """A capitalized two-word phrase matches coaches and teams; a pool-name match
    is a fact. 'Kyle Shanahan' is in the prose and must not survive."""
    found = extract_mentions(strip_tags(COLUMN), "Dave Richard", "u", POOL)
    keys = {m.player_key for m in found}
    assert keys == {"espn-1", "espn-2", "espn-3"}


def test_a_pool_player_the_column_never_names_is_not_mentioned():
    found = extract_mentions(strip_tags(COLUMN), "Dave Richard", "u", POOL)
    assert "espn-4" not in {m.player_key for m in found}


def test_names_match_across_punctuation_spellings():
    """The article writes 'D.J. Moore'; the pool has 'DJ Moore'. Both go through
    `normalize_name`, the same join the ECR match already relies on."""
    found = {m.player_key: m for m in
             extract_mentions(strip_tags(COLUMN), "Dave Richard", "u", POOL)}
    assert "D.J. Moore" in found["espn-3"].snippet


def test_team_defenses_are_never_mentioned():
    """A defense is named after its team, and prose says team names constantly —
    every article about the Chiefs would "mention" the Chiefs D/ST."""
    text = "The Kansas City offense looked great."
    assert extract_mentions(text, "A", "u", POOL) == []


def test_script_and_style_content_is_not_searched():
    """The fixture hides 'Never Mentioned' inside a <script> tag."""
    assert "Never Mentioned" not in strip_tags(COLUMN)


def test_snippet_is_the_sentence_around_the_name():
    found = {m.player_key: m for m in
             extract_mentions(strip_tags(COLUMN), "Dave Richard", "u", POOL)}
    assert "eleven" in found["espn-1"].snippet


def test_index_mentions_groups_by_player():
    mentions = extract_mentions(strip_tags(COLUMN), "Dave Richard", "u", POOL)
    grouped = index_mentions(mentions)
    assert set(grouped) == {"espn-1", "espn-2", "espn-3"}
    assert len(grouped["espn-1"]) == 1


# --- fetching degrades ----------------------------------------------------
def test_a_successful_fetch_records_the_source_for_the_credits_line():
    url = "https://cbs.test/fantasy/football/news/fantasy-football-week-9-waiver-wire-adds/"
    fetcher = ColumnFetcher([SOURCE], session=_FakeSession(
        {SOURCE.index_url: INDEX, url: COLUMN}))
    mentions = fetcher.fetch(9, POOL)
    assert mentions and fetcher.read == [("Dave Richard", url)]


def test_a_paywall_costs_quotes_and_nothing_else(capsys):
    url = "https://cbs.test/fantasy/football/news/fantasy-football-week-9-waiver-wire-adds/"
    fetcher = ColumnFetcher([SOURCE], session=_FakeSession(
        {SOURCE.index_url: INDEX, url: PAYWALL}))
    assert fetcher.fetch(9, POOL) == []
    assert fetcher.read == []
    assert "paywall or layout change" in capsys.readouterr().err


def test_one_fetcher_serves_every_league_without_refetching():
    """The fetcher is shared across all leagues in a run: the writers publish one
    column a week, not one per league. Only the (pure) name extraction re-runs,
    against each league's own pool."""
    url = "https://cbs.test/fantasy/football/news/fantasy-football-week-9-waiver-wire-adds/"
    session = _FakeSession({SOURCE.index_url: INDEX, url: COLUMN})
    fetcher = ColumnFetcher([SOURCE], session=session)

    first = fetcher.fetch(9, POOL)
    second = fetcher.fetch(9, POOL[:1])          # a second league, smaller pool

    assert len(session.calls) == 2, "index + article, fetched once for both leagues"
    assert len(first) == 3 and len(second) == 1  # extraction still per-pool
    assert fetcher.read.count(("Dave Richard", url)) == 1


def test_a_failed_fetch_is_not_retried_for_every_league(capsys):
    """Three leagues shouldn't mean three attempts at a column that 404s."""
    session = _FakeSession({})
    fetcher = ColumnFetcher([SOURCE], session=session)
    for _ in range(3):
        fetcher.fetch(9, POOL)
    assert len(session.calls) == 1


def test_a_network_error_warns_and_returns_empty(capsys):
    fetcher = ColumnFetcher([SOURCE], session=_FakeSession(
        {SOURCE.index_url: requests.RequestException("boom")}))
    assert fetcher.fetch(9, POOL) == []
    assert "couldn't read Dave Richard's column" in capsys.readouterr().err


def test_a_404_warns_and_returns_empty(capsys):
    fetcher = ColumnFetcher([SOURCE], session=_FakeSession({}))
    assert fetcher.fetch(9, POOL) == []
    assert "HTTP 404" in capsys.readouterr().err


def test_one_broken_writer_does_not_stop_the_others():
    good = ColumnSource("Jamey Eisenberg", "https://cbs.test/writers/jamey/",
                        "https://cbs.test")
    url = "https://cbs.test/fantasy/football/news/fantasy-football-week-9-waiver-wire-adds/"
    fetcher = ColumnFetcher([SOURCE, good], session=_FakeSession({
        SOURCE.index_url: requests.RequestException("boom"),
        good.index_url: INDEX,
        url: COLUMN,
    }))
    assert {m.author for m in fetcher.fetch(9, POOL)} == {"Jamey Eisenberg"}


def test_the_shipped_sources_are_the_three_the_user_named():
    from ff_startsit.waivers.columns import SOURCES

    assert [s.author for s in SOURCES] == ["Justin Boone", "Jamey Eisenberg",
                                           "Dave Richard"]
