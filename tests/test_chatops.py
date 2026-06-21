from ff_startsit.chatops import parse_command


def test_lineup_and_report():
    assert parse_command("/lineup") == ["lineup", "--md"]
    assert parse_command("/report") == ["report"]


def test_rank_validates_position():
    assert parse_command("/rank RB") == ["rank", "--pos", "RB", "--md"]
    assert parse_command("/rank wr") == ["rank", "--pos", "WR", "--md"]
    assert parse_command("/rank notapos") is None
    assert parse_command("/rank") is None


def test_compare_splits_on_pipe_keeping_multiword_names():
    assert parse_command("/compare Josh Allen | Jalen Hurts") == [
        "compare", "Josh Allen", "Jalen Hurts", "--md",
    ]


def test_compare_requires_two_names():
    assert parse_command("/compare Josh Allen") is None
    assert parse_command("/compare Josh Allen |") is None


def test_inline_options():
    assert parse_command("/rank RB week 5") == [
        "rank", "--pos", "RB", "--md", "--week", "5",
    ]
    assert parse_command("/lineup source manual") == [
        "lineup", "--md", "--source", "manual",
    ]
    # Options trailing a compare land as flags, names stay clean.
    assert parse_command("/compare A Back | B Back week 3") == [
        "compare", "A Back", "B Back", "--md", "--week", "3",
    ]


def test_ranking_option():
    assert parse_command("/rank RB ranking journalists") == [
        "rank", "--pos", "RB", "--md", "--ranking", "journalists",
    ]
    assert parse_command("/lineup ranking fantasypros") == [
        "lineup", "--md", "--ranking", "fantasypros",
    ]


def test_non_commands_return_none():
    assert parse_command("just a normal comment") is None
    assert parse_command("") is None
    assert parse_command("/unknown thing") is None


def test_uses_first_nonblank_line():
    assert parse_command("\n\n/lineup\nsome trailing prose") == ["lineup", "--md"]
