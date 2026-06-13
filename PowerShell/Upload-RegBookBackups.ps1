# Path to AzCopy.
$azcopy = 'E:\azcopy\azcopy.exe'

# Source folder containing the backup files.
$sourceFolder = 'E:\SQLBACKUP\RegBook'

# Destination blob path: rgp/RegBook/060320206
$destBase = 'https://epuatukseicdatabasecpsa.blob.core.windows.net/rgp/RegBook/060320206'

# SAS token.
$sas = '?sp=racwdl&st='

if (-not (Test-Path -LiteralPath $azcopy)) {
    throw "AzCopy not found: $azcopy"
}

if (-not (Test-Path -LiteralPath $sourceFolder)) {
    throw "Source folder not found: $sourceFolder"
}

Write-Host "Uploading all files from $sourceFolder..."
& $azcopy copy "$sourceFolder\*" "$destBase$sas" --recursive=true --overwrite=true

if ($LASTEXITCODE -ne 0) {
    throw "AzCopy upload failed with exit code $LASTEXITCODE"
}