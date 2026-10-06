from pathlib import Path


def test_streamlit_entrypoint_exists() -> None:
    assert Path(__file__).parents[1].joinpath("src", "app.py").is_file()
