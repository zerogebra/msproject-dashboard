"""
Uses Windows PowerShell + MS Project COM automation to write task
percentage-complete values back into a .mpp file.

Requirements:
  - MS Project must be INSTALLED on this machine
  - The target .mpp file must be CLOSED (not open in MS Project)
"""

import subprocess
import json
import re


def is_msp_file_open(mpp_path: str) -> bool:
    """Return True if MS Project currently has this file open."""
    # Normalise path separators for PowerShell
    safe_path = mpp_path.replace("\\", "\\\\")
    ps = f"""
try {{
    $app = [System.Runtime.InteropServices.Marshal]::GetActiveObject('MSProject.Application')
    foreach ($p in $app.Projects) {{
        if ($p.FullName -eq '{safe_path}') {{ Write-Output 'OPEN'; exit }}
    }}
}} catch {{ }}
Write-Output 'CLOSED'
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, timeout=10
    )
    return "OPEN" in result.stdout


def push_pct_to_msp(mpp_path: str, task_overrides: dict) -> dict:
    """
    Update PercentComplete for each task in task_overrides.

    task_overrides: { "Task Title": {"pct": 45}, ... }

    Returns: {"success": bool, "updated": int, "errors": [...]}
    """
    if not task_overrides:
        return {"success": True, "updated": 0, "errors": []}

    # Build a JSON string of {taskName: pct} for PowerShell to consume
    pct_map = {name: info["pct"] for name, info in task_overrides.items()
               if "pct" in info}
    pct_json = json.dumps(pct_map, ensure_ascii=False).replace("'", "\\'")

    safe_path = mpp_path.replace("\\", "\\\\")

    ps = f"""
$pctMap = '{pct_json}' | ConvertFrom-Json
$mppPath = '{safe_path}'

$app = New-Object -ComObject 'MSProject.Application'
$app.Visible = $false

try {{
    $app.FileOpen($mppPath, $false) | Out-Null
}} catch {{
    Write-Output "ERROR:Cannot open file: $_"
    $app.Quit()
    exit 1
}}

$proj = $app.ActiveProject
$updated = 0
$errors  = @()

foreach ($t in $proj.Tasks) {{
    if ($t -eq $null) {{ continue }}
    $name = $t.Name
    if ($pctMap.PSObject.Properties.Name -contains $name) {{
        $newPct = [int]$pctMap.$name
        try {{
            $t.PercentComplete = $newPct
            $updated++
        }} catch {{
            $errors += "FAIL:$name"
        }}
    }}
}}

$app.FileSave() | Out-Null
$app.FileClose() | Out-Null
$app.Quit()

Write-Output "UPDATED:$updated"
foreach ($e in $errors) {{ Write-Output $e }}
"""

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "updated": 0,
                "errors": ["Timed out after 120 s. Is MS Project hung?"]}

    stdout = result.stdout or ""
    stderr = result.stderr or ""

    if result.returncode != 0 or "ERROR:" in stdout:
        msgs = [l for l in stdout.splitlines() if l.startswith("ERROR:")]
        return {"success": False, "updated": 0,
                "errors": msgs or [stderr.strip() or "Unknown PowerShell error"]}

    m = re.search(r"UPDATED:(\d+)", stdout)
    updated = int(m.group(1)) if m else 0
    errors  = [l for l in stdout.splitlines() if l.startswith("FAIL:")]

    return {
        "success": True,
        "updated": updated,
        "errors":  errors,
    }
