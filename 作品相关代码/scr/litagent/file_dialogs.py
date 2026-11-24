"""Native file and folder pickers for the locally hosted web UI."""
from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional


def _powershell_path() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(
        Path(root)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )


def _run_dialog(script: str) -> List[str]:
    if os.name != "nt":
        raise RuntimeError("原生文件夹选择仅支持 Windows")
    executable = _powershell_path()
    if not Path(executable).is_file():
        raise RuntimeError("找不到 Windows PowerShell")

    with tempfile.TemporaryDirectory(prefix="litagent-dialog-") as temp_dir:
        output = Path(temp_dir) / "selection.txt"
        safe_output = str(output).replace("'", "''")
        wrapped = (
            "$ErrorActionPreference='Stop'\n"
            "Add-Type -AssemblyName System.Windows.Forms\n"
            "[System.Windows.Forms.Application]::EnableVisualStyles()\n"
            f"$outputPath='{safe_output}'\n"
            f"{script}\n"
            "if ($selected.Count -gt 0) {\n"
            "  [System.IO.File]::WriteAllLines("
            "$outputPath,$selected,[System.Text.UTF8Encoding]::new($false))\n"
            "}\n"
        )
        encoded = base64.b64encode(
            wrapped.encode("utf-16le")
        ).decode("ascii")
        completed = subprocess.run(
            [
                executable,
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(message or "打开系统文件选择框失败")
        if not output.is_file():
            return []
        return [
            line.strip()
            for line in output.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]


def choose_local_folder(initial_dir: Optional[str] = None) -> Optional[Path]:
    """Open a native Windows folder picker."""
    initial = ""
    if initial_dir and Path(initial_dir).expanduser().is_dir():
        initial = str(Path(initial_dir).expanduser().resolve()).replace("'", "''")
    script = f"""
$dialog=New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description='选择需要导入 lit-agent 的文献文件夹'
$dialog.ShowNewFolderButton=$false
$dialog.SelectedPath='{initial}'
$selected=@()
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
  $selected=@($dialog.SelectedPath)
}}
"""
    paths = _run_dialog(script)
    return Path(paths[0]) if paths else None


def choose_local_files() -> List[Path]:
    """Open a native Windows multi-file picker for supported document types."""
    script = """
$dialog=New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title='选择需要导入 lit-agent 的文献文件'
$dialog.Multiselect=$true
$dialog.CheckFileExists=$true
$dialog.Filter='支持的文档|*.pdf;*.docx;*.doc;*.rtf;*.txt;*.md;*.markdown;*.text;*.html;*.htm|所有文件|*.*'
$selected=@()
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  $selected=@($dialog.FileNames)
}
"""
    return [Path(path) for path in _run_dialog(script)]


__all__ = ["choose_local_files", "choose_local_folder"]
