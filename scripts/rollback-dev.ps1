[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^dev-v[1-9][0-9]*$')]
    [string]$TargetTag,

    [switch]$Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Region     = "us-east-1"
$AccountId  = "672580927557"
$Repository = "pocket-tts-dev"
$Function   = "pocket-tts-dev"

$Registry = "$AccountId.dkr.ecr.$Region.amazonaws.com"
$EcrUri   = "$Registry/$Repository"

function Assert-LastExitCode {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Operation
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE"
    }
}

Write-Host ""
Write-Host "=== Pocket TTS DEV rollback ==="
Write-Host ""

# ------------------------------------------------------------
# 1. AWS account boundary
# ------------------------------------------------------------

$CurrentAccount = (
    aws sts get-caller-identity `
        --query Account `
        --output text
).Trim()

Assert-LastExitCode "Reading AWS account identity"

if ($CurrentAccount -ne $AccountId) {
    throw "Wrong AWS account. Expected $AccountId but authenticated to $CurrentAccount"
}

Write-Host "AWS account        : $CurrentAccount"

# ------------------------------------------------------------
# 2. Repository must remain immutable
# ------------------------------------------------------------

$TagMutability = (
    aws ecr describe-repositories `
        --repository-names $Repository `
        --region $Region `
        --query 'repositories[0].imageTagMutability' `
        --output text
).Trim()

Assert-LastExitCode "Reading ECR repository configuration"

if ($TagMutability -ne "IMMUTABLE") {
    throw "ECR repository is not IMMUTABLE. Found '$TagMutability'."
}

Write-Host "ECR mutability     : $TagMutability"

# ------------------------------------------------------------
# 3. Resolve requested rollback tag to immutable digest
# ------------------------------------------------------------

$TargetDigest = (
    aws ecr describe-images `
        --repository-name $Repository `
        --image-ids "imageTag=$TargetTag" `
        --region $Region `
        --query 'imageDetails[0].imageDigest' `
        --output text
).Trim()

Assert-LastExitCode "Resolving rollback tag '$TargetTag'"

if (
    [string]::IsNullOrWhiteSpace($TargetDigest) -or
    $TargetDigest -eq "None"
) {
    throw "Could not resolve '$TargetTag' to an ECR digest."
}

$ManifestType = (
    aws ecr describe-images `
        --repository-name $Repository `
        --image-ids "imageTag=$TargetTag" `
        --region $Region `
        --query 'imageDetails[0].imageManifestMediaType' `
        --output text
).Trim()

Assert-LastExitCode "Reading rollback image manifest type"

$ExpectedManifestType = "application/vnd.docker.distribution.manifest.v2+json"

if ($ManifestType -ne $ExpectedManifestType) {
    throw "Unexpected image manifest '$ManifestType'. Expected '$ExpectedManifestType'."
}

$TargetImageUri = "$EcrUri@$TargetDigest"

Write-Host "Target tag         : $TargetTag"
Write-Host "Target digest      : $TargetDigest"
Write-Host "Target image       : $TargetImageUri"
Write-Host "Manifest type      : $ManifestType"

# ------------------------------------------------------------
# 4. Snapshot live Lambda state
# ------------------------------------------------------------

$LiveBefore = aws lambda get-function `
    --function-name $Function `
    --region $Region `
    --output json |
    ConvertFrom-Json

Assert-LastExitCode "Reading current Lambda state"

$CurrentImageUri = $LiveBefore.Code.ResolvedImageUri
$CurrentDigest   = ($CurrentImageUri -split '@')[-1]
$BeforeRevision  = $LiveBefore.Configuration.RevisionId
$FunctionState   = $LiveBefore.Configuration.State
$UpdateStatus    = $LiveBefore.Configuration.LastUpdateStatus

if ($FunctionState -ne "Active") {
    throw "Lambda state is '$FunctionState', not Active."
}

if ($UpdateStatus -ne "Successful") {
    throw "Lambda LastUpdateStatus is '$UpdateStatus', not Successful."
}

Write-Host ""
Write-Host "Current image      : $CurrentImageUri"
Write-Host "Current digest     : $CurrentDigest"
Write-Host "RevisionId         : $BeforeRevision"

if ($CurrentDigest -eq $TargetDigest) {
    throw "Lambda already runs '$TargetTag' digest $TargetDigest. Nothing to roll back."
}

# ------------------------------------------------------------
# 5. Safe default: planning only
# ------------------------------------------------------------

Write-Host ""
Write-Host "ROLLBACK PLAN VERIFIED"
Write-Host "FROM               : $CurrentDigest"
Write-Host "TO TAG             : $TargetTag"
Write-Host "TO DIGEST          : $TargetDigest"
Write-Host ""

if (-not $Execute) {
    Write-Host "Dry run only. Lambda was NOT changed."
    Write-Host "Re-run with -Execute only when an actual rollback is intended."
    exit 0
}

# ------------------------------------------------------------
# 6. Deploy exact old digest with optimistic concurrency
# ------------------------------------------------------------

Write-Host "EXECUTING ROLLBACK..."

aws lambda update-function-code `
    --function-name $Function `
    --image-uri $TargetImageUri `
    --revision-id $BeforeRevision `
    --region $Region `
    --output json |
    Out-Null

Assert-LastExitCode "Updating Lambda rollback image"

aws lambda wait function-updated-v2 `
    --function-name $Function `
    --region $Region

Assert-LastExitCode "Waiting for Lambda rollback"

# ------------------------------------------------------------
# 7. Post-rollback proof
# ------------------------------------------------------------

$LiveAfter = aws lambda get-function `
    --function-name $Function `
    --region $Region `
    --output json |
    ConvertFrom-Json

Assert-LastExitCode "Reading rolled-back Lambda"

$ResolvedImageUri = $LiveAfter.Code.ResolvedImageUri
$ResolvedDigest   = ($ResolvedImageUri -split '@')[-1]
$AfterRevision    = $LiveAfter.Configuration.RevisionId
$AfterStatus      = $LiveAfter.Configuration.LastUpdateStatus

if ($AfterStatus -ne "Successful") {
    throw "Rollback finished with LastUpdateStatus '$AfterStatus'."
}

if ($ResolvedDigest -ne $TargetDigest) {
    throw "Rollback digest mismatch. Expected $TargetDigest but Lambda resolved $ResolvedDigest"
}

Write-Host ""
Write-Host "======================================================"
Write-Host "ROLLBACK VERIFIED"
Write-Host "======================================================"
Write-Host "Target tag         : $TargetTag"
Write-Host "Target digest      : $TargetDigest"
Write-Host "Lambda image       : $ResolvedImageUri"
Write-Host "Previous revision  : $BeforeRevision"
Write-Host "Current revision   : $AfterRevision"
Write-Host "Update status      : $AfterStatus"
Write-Host "======================================================"