import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]


def test_nemo_embed_extra_provisions_the_runtime_dependencies() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert project["project"]["optional-dependencies"]["nemo-embed"] == [
        "sentence-transformers>=5.1",
        "torch>=2.7",
        "transformers>=4.44",
    ]
    assert project["tool"]["uv"]["sources"]["torch"] == [
        {
            "index": "pytorch-cu128",
            "extra": "nemo-embed",
            "marker": "sys_platform == 'linux' or sys_platform == 'win32'",
        }
    ]
    assert project["tool"]["uv"]["index"] == [
        {
            "name": "pytorch-cu128",
            "url": "https://download.pytorch.org/whl/cu128",
            "explicit": True,
        }
    ]
