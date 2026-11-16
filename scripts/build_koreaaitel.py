"""Build the branded PyPI distribution without duplicating SDK modules."""

import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from runpy import run_path


root = Path(__file__).resolve().parents[1]
project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
version = run_path(str(root / "src/clawops/_version.py"))["__version__"]
legacy_name = project["name"]
requirement = f"{legacy_name}=={version}"
destination = Path(sys.argv[1]).resolve()

with tempfile.TemporaryDirectory() as work:
    staging = Path(work)
    metadata = [
        "[build-system]",
        'requires = ["setuptools>=77"]',
        'build-backend = "setuptools.build_meta"',
        "",
        "[project]",
        'name = "koreaaitel"',
        f"version = {json.dumps(version)}",
        f"description = {json.dumps(project['description'], ensure_ascii=False)}",
        f"requires-python = {json.dumps(project['requires-python'])}",
        f"license = {json.dumps(project['license'])}",
        'license-files = ["LICENSE"]',
        'readme = "README.md"',
        f"dependencies = {json.dumps([requirement])}",
        "",
        "[project.urls]",
    ]
    metadata.extend(f"{label} = {json.dumps(url)}" for label, url in project["urls"].items())
    metadata.extend(["", "[project.optional-dependencies]"])
    for extra in project["optional-dependencies"]:
        metadata.append(f"{extra} = {json.dumps([f'{legacy_name}[{extra}]=={version}'])}")
    metadata.extend(["", "[tool.setuptools]", "py-modules = []", ""])

    (staging / "pyproject.toml").write_text("\n".join(metadata), encoding="utf-8")
    (staging / "README.md").write_text(
        "# 한국AI통신 (Korea AI Telecom) Python SDK\n\n"
        "Install the official Python SDK with `pip install koreaaitel`. "
        "It installs the matching `clawops` release, so Python imports stay the same.\n\n"
        "```python\nfrom clawops import ClawOps, AsyncClawOps\n```\n\n"
        "[Official documentation](https://docs.koreaaitel.com) · "
        "[Source and examples](https://github.com/koreaaitel/sdk-python)\n",
        encoding="utf-8",
    )
    shutil.copyfile(root / "LICENSE", staging / "LICENSE")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(destination), str(staging)],
        check=True,
    )
