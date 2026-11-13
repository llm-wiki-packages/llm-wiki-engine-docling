"""`llm-wiki-engine-docling prepare|serve`: fetch Docling's models, then serve the request store as a jailed daemon.

The machine-config contract, "An engine's user service", owns the argv:

    llm-wiki-engine-docling prepare --data <data>/engines/docling
    llm-wiki-engine-docling serve --store <dir> --cache <dir> --data <dir> [--options <json>]

`prepare` runs unjailed and on the network, attended. `serve` hands the kit a factory, and the kit runs it only in
its inner process, inside the jail, so Docling is never imported before the jail.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from llm_wiki_engine_kit import serve as kit_serve

ENGINE = "docling"
# The models the converter below uses, and nothing else: `docling-tools` names them, and the ONNX layout model is
# one Hugging Face snapshot beside them.
TOOL_MODELS = ("layout", "tableformer", "rapidocr")
ONNX_LAYOUT_REPO = "docling-project/docling-layout-heron-onnx"
DEFAULT_CPU_THREADS = 4


def models_dir(data: Path) -> Path:
    return data / "models"


def hf_dir(data: Path) -> Path:
    return data / "hf"


def _tools_binary() -> str:
    """`docling-tools` from this venv, never a stranger on PATH."""
    return str(Path(sys.executable).with_name("docling-tools"))


def prepare(data: Path) -> int:
    """Fill `<data>/models/` with the models the converter uses; on the network, unjailed."""
    models, hf = models_dir(data), hf_dir(data)
    models.mkdir(parents=True, exist_ok=True)
    hf.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HF_HOME": str(hf), "DO_NOT_TRACK": "1"}
    env.pop("HF_HUB_OFFLINE", None)
    done = subprocess.run([_tools_binary(), "models", "download", *TOOL_MODELS, "-o", str(models)], env=env)
    if done.returncode != 0:
        print(f"docling-tools models download exited {done.returncode}", file=sys.stderr)
        return done.returncode
    # The snapshot runs in a child, so HF_HOME is set before huggingface_hub reads it at import.
    snapshot = (
        "import sys; from huggingface_hub import snapshot_download; "
        "snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2])"
    )
    target = models / ONNX_LAYOUT_REPO.replace("/", "--")
    done = subprocess.run([sys.executable, "-I", "-c", snapshot, ONNX_LAYOUT_REPO, str(target)], env=env)
    if done.returncode != 0:
        print(f"the {ONNX_LAYOUT_REPO} snapshot exited {done.returncode}", file=sys.stderr)
        return done.returncode
    print(f"models ready in {models}")
    return 0


def cpu_threads(options: dict) -> int:
    value = options.get("cpu_threads", DEFAULT_CPU_THREADS)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SystemExit(f"options.cpu_threads must be an int, 1 or more, not {value!r}")
    return value


def converter_factory(models: Path, threads: int):
    """The factory the kit calls inside the jail: the spike's converter, ONNX layout and RapidOCR on onnxruntime."""

    def make_converter(_daemon: dict):
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import ConversionStatus, InputFormat
        from docling.datamodel.object_detection_engine_options import OnnxRuntimeObjectDetectionEngineOptions
        from docling.datamodel.pipeline_options import (
            LayoutObjectDetectionOptions,
            PdfPipelineOptions,
            RapidOcrOptions,
        )
        from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

        options = PdfPipelineOptions(artifacts_path=str(models))
        options.layout_options = LayoutObjectDetectionOptions(engine_options=OnnxRuntimeObjectDetectionEngineOptions())
        options.ocr_options = RapidOcrOptions(backend="onnxruntime")
        options.accelerator_options = AcceleratorOptions(num_threads=threads, device=AcceleratorDevice.CPU)
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=options),
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
            }
        )

        def convert(body: Path, out: Path, _request: dict) -> dict | None:
            result = converter.convert(body, raises_on_error=False)
            if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
                return {"reason": "unreadable", "traits": []}
            text = result.document.export_to_markdown()
            if not text.strip():
                return {"reason": "empty", "traits": []}
            out.write_text(text, encoding="utf-8")
            return None

        return convert

    return make_converter


def run_serve(args: argparse.Namespace, argv: list[str]) -> int:
    data = Path(args.data).absolute()
    try:
        options = json.loads(args.options)
    except ValueError as exc:
        raise SystemExit(f"--options is not JSON: {exc}") from None
    if not isinstance(options, dict):
        raise SystemExit("--options must be a JSON object")
    threads = cpu_threads(options)
    models, hf = models_dir(data), hf_dir(data)
    return kit_serve(
        ENGINE,
        converter_factory(models, threads),
        threads=1,
        reads=[models, hf],
        env={"HF_HOME": str(hf), "OMP_NUM_THREADS": str(threads)},
        argv=argv,
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="llm-wiki-engine-docling")
    commands = parser.add_subparsers(dest="command", required=True)
    ready = commands.add_parser("prepare", help="download the models the converter uses (network, unjailed)")
    ready.add_argument("--data", required=True, help="<data>/engines/docling")
    run = commands.add_parser("serve", help="answer the docling request store, jailed")
    run.add_argument("--store", required=True, help="<state>/engine-requests/docling")
    run.add_argument("--cache", required=True, help="<cache>/engines/docling; the scratch is its tmp/")
    run.add_argument("--data", required=True, help="<data>/engines/docling, as prepare filled it")
    run.add_argument("--options", default="{}", help="the engine's options as one JSON object")
    run.add_argument("--unjailed", action="store_true", help=argparse.SUPPRESS)
    run.add_argument("--inner", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        return prepare(Path(args.data).absolute())
    return run_serve(args, argv[1:])


if __name__ == "__main__":
    sys.exit(main())
