"""`prepare` fetches every model the converter uses: a conversion after it runs with the hub offline.

By hand only, from the engine's own venv; no suite collects this tree, because no suite has Docling:

    LLM_WIKI_TEST_DOCLING=1 uv run --project llm-wiki-engines/docling pytest llm-wiki-engines/docling/tests
"""

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ENABLE_ENV = "LLM_WIKI_TEST_DOCLING"
PDF_ENV = "LLM_WIKI_TEST_DOCLING_PDF"
# A synthetic scan from the 2026-11-10 extract spike: no text layer, so OCR, layout and the table model all run.
SPIKE_SCAN = Path.home() / "extract-spike" / "corpus" / "synth" / "scanned-Copyright_Permissions_Table_--6dc40c82.pdf"

pytestmark = pytest.mark.skipif(os.environ.get(ENABLE_ENV) != "1", reason=f"set {ENABLE_ENV}=1 to fetch the models")

CONVERT = """
import sys
from pathlib import Path
from llm_wiki_engine_docling.__main__ import converter_factory
convert = converter_factory(Path(sys.argv[1]), 2)({})
out = Path(sys.argv[3])
print(convert(Path(sys.argv[2]), out, {}))
"""


def _reachable(host: str) -> bool:
    try:
        socket.create_connection((host, 443), timeout=5).close()
    except OSError:
        return False
    return True


# The download and a CPU scan conversion each take minutes, past the root config's timeout.
@pytest.mark.timeout(1800)
def test_prepare_then_an_offline_conversion_finds_every_model(tmp_path):
    pdf = Path(os.environ.get(PDF_ENV) or SPIKE_SCAN)
    if not pdf.is_file():
        pytest.skip(f"no scan at {pdf}; name one in {PDF_ENV}")
    if not _reachable("huggingface.co"):
        pytest.skip("huggingface.co is not reachable")
    data = tmp_path / "data"
    env = {key: value for key, value in os.environ.items() if not key.startswith("HF_")}
    done = subprocess.run([sys.executable, "-m", "llm_wiki_engine_docling", "prepare", "--data", str(data)], env=env)
    assert done.returncode == 0

    out = tmp_path / "output.md"
    offline = {**env, "HF_HOME": str(data / "hf"), "HF_HUB_OFFLINE": "1", "HOME": str(tmp_path)}
    done = subprocess.run(
        [sys.executable, "-c", CONVERT, str(data / "models"), str(pdf), str(out)],
        env=offline,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-4000:]
    assert done.stdout.strip().splitlines()[-1] == "None", done.stdout
    assert len(out.read_text(encoding="utf-8").split()) > 20
