# llm-wiki-engine-docling

[Docling](https://github.com/docling-project/docling) as a local extract
engine for LLM-wiki. It reads scans, page layout and tables, on the CPU.

The package ships two artifacts:

- the tool `llm-wiki-engine-docling`, with two commands:
  - `prepare` downloads the models the converter uses;
  - `serve` runs the daemon that answers the engine request store;
- the engine `docling` (`engine/manifest.json`), which the engine pool
  reads.

The engine is async. The CLI submits a file to the machine's request store,
and the daemon answers it. The CLI never starts the daemon. The operator
installs it once, as a user service.

The daemon is built on the engine kit, `llm-wiki-engine-kit`. The kit lives
in `simple10/llm-wiki-plugins` at `llm-wiki-engines/kit/`.
`pyproject.toml` pins it to one commit there.

## Requirements

- Linux. The user service refuses on macOS for now (llm-wiki-plugins #4770).
- `llm-wiki-cli` at ops 0.21.0 or later.
- `nono` on `PATH`. The daemon refuses to run without it.
- `uv`. It installs Python 3.14 for the tool (`.python-version`), because
  onnxruntime 1.31.1 has no wheel for a later Python.
- Read access to `simple10/llm-wiki-plugins` from git, for the kit.

The tool's venv is about 1.5 GB. The models are about 730 MB.

## Turn it on

Do these steps once per machine, on the host:

1. Install the package. Packages pin a release tag:

   ```sh
   llm-wiki-cli machine install llm-wiki-packages/llm-wiki-engine-docling@v0.1.1
   ```

   It installs the `llm-wiki-engine-docling` tool and links the `docling`
   engine artifact.
2. Declare the engine in the machine config (`llm-wiki-cli machine home`
   names its directory):

   ```toml
   [engines.docling]
   package = "llm-wiki-packages/llm-wiki-engine-docling"
   ```

3. Install the daemon:

   ```sh
   llm-wiki-cli machine engines service install docling
   ```

   It downloads the models into `<data>/engines/docling/`, then writes and
   starts the user unit `llm-wiki-engine-docling`.
4. In each wiki that uses it, send scans to it in `.llm-wiki.local.toml`.
   `builtin` reads a PDF with a text layer, and a scan falls through to
   Docling:

   ```toml
   [convert]
   rules = [{ match = { suffix = [".pdf"] }, engines = ["builtin", "docling"], fallback_on = ["no_text_layer"] }]
   ```

5. Check it. `llm-wiki-cli machine doctor` shows `engine-service/docling`
   as `ok`, and `llm-wiki-ops engines list` shows `docling` as `ready`.
6. Try one file:

   ```sh
   llm-wiki-ops extract ~/Downloads/scan.pdf wait=10m
   ```

What to expect:

- The idle daemon holds about 450 MB.
- A conversion holds about 2.3 GB at the default `cpu_threads = 4`.
- The first file after a start also loads the models, so it takes longer.

To change the threads, set `[engines.docling.options] cpu_threads = <n>` in
the machine config. Then run step 3 again: the unit carries the options.

To remove the daemon, run `llm-wiki-cli machine engines service remove docling`.

## What the jail guarantees

`serve` jails itself before it imports Docling. The outer process re-runs
itself under `nono run -s --block-net`. Only the inner process builds the
converter. The jail grants:

- read: the tool's own Python, the models and the Hugging Face directory,
  and the directory the engine's code imports from;
- write: the engine's request store folder and one scratch directory.

The jail has no network. It never reads the home or a wiki.

One daemon serves every wiki on the machine, because the request store is
machine-wide. So a parser exploit inside the jail can answer any wiki's
pending requests with forged text. It can do that until the inner process
recycles: after 50 files, or after any converter exception. It cannot read
a wiki, the network, or anything outside its grants.

Forged text is a content risk, the same as a hostile document's own text.
The host treats every byte of an answer as hostile when it lands it.

## Live tests

The tests need Docling, its models, the network and `nono`. So they run by
hand only, behind `LLM_WIKI_TEST_DOCLING=1`. From the repo root:

```sh
LLM_WIKI_TEST_DOCLING=1 uv run pytest -s tests
```

- `tests/test_docling_models.py` runs `prepare` into an empty directory,
  then converts one scan with the hub offline. A missing model fails it.
- `tests/test_docling_live.py` runs `serve` jailed and sends it a text PDF
  and a scan through a real request store.

Variables:

- `LLM_WIKI_TEST_DOCLING_DATA` names a data directory that `prepare`
  already filled. The live test then skips the download.
- `LLM_WIKI_TEST_DOCLING_PDF` names the scan for the models test.

The default documents are from the extract spike corpus under
`~/extract-spike/corpus/`. A test skips when its document is missing.

There is no CI workflow. No test runs without Docling's models, and the
install alone is about 1.5 GB.

## License

MIT. See `LICENSE`.
