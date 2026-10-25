import pytest

from civ_mcp.broadcast import append_notebook_file


def test_append_under_section(tmp_path):
    p = tmp_path / "NOTEBOOK.md"
    p.write_text("# Notebook\n\n## Standing lessons\n\n## Game log\n")
    append_notebook_file(p, "Game log", "Game 1 lost to Scythia T41.")
    txt = p.read_text()
    assert txt.index("## Game log") < txt.index("Game 1 lost")
    assert "—" not in txt


def test_unknown_section_rejected(tmp_path):
    with pytest.raises(ValueError):
        append_notebook_file(tmp_path / "n.md", "Misc", "x")


def test_bullet_lands_in_right_section_and_keeps_order(tmp_path):
    p = tmp_path / "N.md"
    p.write_text(
        "# Notebook\n\n## Standing lessons\n\n- old\n\n## Game log\n\n## Current hypothesis\n"
    )
    append_notebook_file(p, "Standing lessons", "new lesson")
    append_notebook_file(p, "Standing lessons", "second")
    txt = p.read_text()
    assert txt.index("- old") < txt.index("new lesson") < txt.index("second")
    assert txt.index("second") < txt.index("## Game log")
    assert "- " in txt.split("new lesson")[0].splitlines()[-1]


def test_missing_heading_and_file_created(tmp_path):
    p = tmp_path / "sub" / "N.md"
    append_notebook_file(p, "Current hypothesis", "Walls first.")
    txt = p.read_text()
    assert "## Current hypothesis" in txt and "Walls first." in txt
    p.write_text("# Notebook\n\n## Game log\n- a\n")
    append_notebook_file(p, "Standing lessons", "zebra")
    txt = p.read_text()
    assert "## Standing lessons" in txt and txt.index("zebra") > txt.index("## Standing lessons")


def test_text_with_em_dash_sanitised_and_multiline_collapsed(tmp_path):
    p = tmp_path / "N.md"
    p.write_text("## Game log\n")
    append_notebook_file(p, "Game log", "lost — badly\nsecond line")
    txt = p.read_text()
    assert "—" not in txt and txt.count("\n- ") == 1
