param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
& $Python -m pip install --upgrade pyinstaller
& $Python -m PyInstaller --clean --noconfirm persian-comment-crawler.spec
Write-Host "Built dist/PersianCommentCrawler/PersianCommentCrawler.exe"
