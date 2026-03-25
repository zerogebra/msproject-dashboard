param(
    [string]$MppRoot = "C:\Users\mohammadhamzehCubesP\OneDrive - Cubesplatform\Desktop\Cubes\Project Plans",
    [string]$OutputPath = "C:\Users\mohammadhamzehCubesP\Downloads\Project Monitoring\static\portfolio.json"
)

$ErrorActionPreference = "Stop"

try {
    $projApp = New-Object -ComObject "MSProject.Application"
    $projApp.Visible = $false
} catch {
    Write-Error "Could not create MSProject COM object. Ensure Microsoft Project is installed."
    exit 1
}

$projects = @()

Get-ChildItem -Path $MppRoot -Filter *.mpp -Recurse | ForEach-Object {
    $file = $_
    try {
        $projApp.FileOpen($file.FullName)
        $project = $projApp.ActiveProject

        $items = @()
        foreach ($t in $project.Tasks) {
            if ($null -ne $t -and $t.Name) {
                $items += [pscustomobject]@{
                    title = $t.Name
                    start = $t.Start
                    end   = $t.Finish
                    pct   = [double]($t.PercentComplete)
                }
            }
        }

        $projName = $project.Name
        if (-not $projName) {
            $projName = $file.BaseName
        }

        $codeLen = [Math]::Min(3, $projName.Length)
        $code = $projName.Substring(0, $codeLen).ToUpper()

        $projects += [pscustomobject]@{
            code  = $code
            name  = $projName
            items = $items
        }
    } catch {
        # Ignore files that can't be read
    } finally {
        $projApp.FileCloseAll(0)
    }
}

$payload = [pscustomobject]@{
    projects = $projects
}

$dir = Split-Path -Path $OutputPath -Parent
if (-not (Test-Path $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}

$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $OutputPath -Encoding UTF8

$projApp.Quit()

Write-Host "Export complete. JSON written to $OutputPath"

