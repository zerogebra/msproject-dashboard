param(
    [string]$Message = "Update"
)

Set-Location "c:\Users\mohammadhamzehCubesP\Downloads\Project Monitoring"

git add .
git commit -m $Message
git push

Write-Host ""
Write-Host "Done! Code pushed to GitHub." -ForegroundColor Green
Write-Host "https://github.com/zerogebra/msproject-dashboard" -ForegroundColor Cyan
