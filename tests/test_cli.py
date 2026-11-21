import pytest

from ftf.cli import main


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no stray .env from the repo
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def test_ingest_of_empty_folder_fails_loudly(data_dir, tmp_path, capsys):
    # On a fresh clone Docker mounts a freshly created, empty ./testing_sample at /sample.
    empty = tmp_path / "sample"
    empty.mkdir()
    with pytest.raises(SystemExit) as exc:
        main(["ingest", str(empty)])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "no views" in err and "FTF_SAMPLE_DIR" in err


def test_ingest_with_material_succeeds(data_dir, tmp_path, capsys):
    incident = tmp_path / "sample" / "1"
    incident.mkdir(parents=True)
    (incident / "page.html").write_text("<html><title>x</title></html>")
    main(["ingest", str(tmp_path / "sample")])
    out = capsys.readouterr().out
    assert "incident 1: 1 page" in out
    assert "observations:" in out
