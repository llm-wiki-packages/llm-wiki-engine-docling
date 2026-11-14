"""The Docling daemon, jailed, answers a text PDF and a scan from the spike corpus through a real request store.

By hand only, from the repo root, in the engine's own venv:

    LLM_WIKI_TEST_DOCLING=1 uv run pytest -s tests

`LLM_WIKI_TEST_DOCLING_DATA` names a data directory `prepare` already filled (`<data>/engines/docling`), and skips
the download. Unset, the test runs `prepare` first, on the network.
"""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

ENABLE_ENV = "LLM_WIKI_TEST_DOCLING"
DATA_ENV = "LLM_WIKI_TEST_DOCLING_DATA"
CORPUS = Path.home() / "extract-spike" / "corpus"
TEXT_PDF = CORPUS / "pdf" / "Copyright_Permissions_Table_--6dc40c82.pdf"
SCAN_PDF = CORPUS / "synth" / "scanned-Copyright_Permissions_Table_--6dc40c82.pdf"
# A row of the scanned table: only OCR and the table model put it in the text.
SCAN_LINE = "Students Walking on Campus"
# Outside the temp dir, which nono's default groups grant write on.
LIVE_ROOT = Path.home() / ".cache" / "llm-wiki-engine-docling-test"
WAIT = 1500

pytestmark = pytest.mark.skipif(os.environ.get(ENABLE_ENV) != "1", reason=f"set {ENABLE_ENV}=1 to run Docling")


def _folder(store: Path, pdf: Path) -> Path:
    folder = store / uuid.uuid4().hex
    folder.mkdir(parents=True)
    shutil.copyfile(pdf, folder / "body.pdf")
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    request = {"v": 1, "engine": "docling", "section": "convert", "sha256": sha, "body": "body.pdf", "options": {}}
    (folder / "request.json").write_text(json.dumps(request), encoding="utf-8")
    return folder


@pytest.fixture
def corpus():
    """Skips before `data` downloads the models: a missing document costs no 730 MB fetch."""
    for pdf in (TEXT_PDF, SCAN_PDF):
        if not pdf.is_file():
            pytest.skip(f"no spike corpus file at {pdf}")


@pytest.fixture
def data(tmp_path, corpus):
    named = os.environ.get(DATA_ENV)
    if named:
        return Path(named)
    data = tmp_path / "data"
    done = subprocess.run([sys.executable, "-m", "llm_wiki_engine_docling", "prepare", "--data", str(data)])
    assert done.returncode == 0
    return data


@pytest.fixture
def live():
    if shutil.which("nono") is None:
        pytest.fail("no nono on PATH: the daemon refuses to run unjailed")
    root = LIVE_ROOT / f"docling-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


# Two CPU conversions, the first loading every model, past the root config's timeout.
@pytest.mark.timeout(WAIT + 120)
def test_the_jailed_daemon_answers_a_text_pdf_and_a_scan(data, live):
    store = live / "store"
    folders = {pdf: _folder(store, pdf) for pdf in (TEXT_PDF, SCAN_PDF)}
    argv = [sys.executable, "-m", "llm_wiki_engine_docling", "serve", "--store", str(store)]
    argv += ["--cache", str(live / "cache"), "--data", str(data), "--options", json.dumps({"cpu_threads": 4})]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    started = time.monotonic()
    try:
        for pdf, folder in folders.items():
            while not (folder / "result.json").exists():
                if proc.poll() is not None:
                    pytest.fail(f"the daemon exited {proc.returncode}: {proc.communicate()[1].decode()[-4000:]}")
                if time.monotonic() - started > WAIT:
                    pytest.fail(f"no answer for {pdf.name} after {WAIT}s")
                time.sleep(1)
            print(f"{pdf.name}: answered {time.monotonic() - started:.1f}s after the start")
        children = Path(f"/proc/{proc.pid}/task/{proc.pid}/children").read_text(encoding="utf-8").split()
        assert [Path(f"/proc/{one}/comm").read_text(encoding="utf-8").strip() for one in children] == ["nono"]
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.communicate(timeout=60)
    for pdf, folder in folders.items():
        assert json.loads((folder / "result.json").read_text(encoding="utf-8"))["reason"] is None, pdf.name
        assert (folder / "output.md").read_text(encoding="utf-8").strip(), pdf.name
    assert SCAN_LINE in (folders[SCAN_PDF] / "output.md").read_text(encoding="utf-8")
