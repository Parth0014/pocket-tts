[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^dev-v[1-9][0-9]*$')]
    [string]$ReleaseTag,

    [string]$ExpectedBranch = "dev/aws-pipeline",

    [switch]$PreflightOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ------------------------------------------------------------------
# Fixed deployment boundary
# ------------------------------------------------------------------

$Region      = "us-east-1"
$AccountId   = "672580927557"
$Repository  = "pocket-tts-dev"
$Function    = "pocket-tts-dev"

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

function Test-EcrTagExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Tag
    )

    $Query = "imageIds[?imageTag=='$Tag'].imageTag | [0]"

    $Result = aws ecr list-images `
        --repository-name $Repository `
        --region $Region `
        --filter tagStatus=TAGGED `
        --query $Query `
        --output text

    Assert-LastExitCode "Checking ECR tag '$Tag'"

    if ([string]::IsNullOrWhiteSpace($Result)) {
        return $false
    }

    if ($Result.Trim() -eq "None") {
        return $false
    }

    return $true
}

Write-Host ""
Write-Host "=== Pocket TTS DEV deployment preflight ==="
Write-Host ""

# ------------------------------------------------------------------
# 1. AWS identity
# ------------------------------------------------------------------

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

# ------------------------------------------------------------------
# 2. Git identity
# ------------------------------------------------------------------

$GitBranch = (git branch --show-current).Trim()
Assert-LastExitCode "Reading Git branch"

if ($GitBranch -ne $ExpectedBranch) {
    throw "Wrong Git branch. Expected '$ExpectedBranch' but found '$GitBranch'"
}

$GitStatus = git status --porcelain
Assert-LastExitCode "Reading Git working tree"

if ($GitStatus) {
    throw "Git working tree is dirty. Commit or remove changes before deployment."
}

$GitSha = (git rev-parse HEAD).Trim()
Assert-LastExitCode "Reading Git SHA"

$GitShort = (git rev-parse --short HEAD).Trim()
Assert-LastExitCode "Reading short Git SHA"

$GitTag = "git-$GitShort"

Write-Host "Git branch         : $GitBranch"
Write-Host "Git SHA            : $GitSha"
Write-Host "Release tag        : $ReleaseTag"
Write-Host "Git image tag      : $GitTag"

# ------------------------------------------------------------------
# 3. ECR repository safety
# ------------------------------------------------------------------

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

if (Test-EcrTagExists -Tag $ReleaseTag) {
    throw "Release tag '$ReleaseTag' already exists. Immutable tags must never be reused."
}

if (Test-EcrTagExists -Tag $GitTag) {
    throw "Git tag '$GitTag' already exists. Refusing to rebuild the same deployment identity."
}

Write-Host "Release tag free   : YES"
Write-Host "Git tag free       : YES"

# ------------------------------------------------------------------
# 4. Worker S3 permission contract
# ------------------------------------------------------------------

$WorkerRoleArn = (
    aws lambda get-function-configuration `
        --function-name $Function `
        --region $Region `
        --query Role `
        --output text
).Trim()

Assert-LastExitCode "Reading Worker execution role"

if ([string]::IsNullOrWhiteSpace($WorkerRoleArn)) {
    throw "Worker execution role ARN is empty."
}

$WorkerRoleName = ($WorkerRoleArn -split '/')[-1]

$WorkerS3PolicyRaw = aws iam get-role-policy `
    --role-name $WorkerRoleName `
    --policy-name PocketTTSDevS3Access `
    --output json

Assert-LastExitCode "Reading Worker DEV S3 inline policy"

$WorkerS3Policy = $WorkerS3PolicyRaw | ConvertFrom-Json

$ExpectedDevBucketArn = "arn:aws:s3:::pocket-tts-dev-test"
$HasDevListBucket = $false

foreach ($Statement in @($WorkerS3Policy.PolicyDocument.Statement)) {
    $Actions = @($Statement.Action)
    $Resources = @($Statement.Resource)

    if (
        $Statement.Effect -eq "Allow" -and
        $Actions -contains "s3:ListBucket" -and
        $Resources -contains $ExpectedDevBucketArn
    ) {
        $HasDevListBucket = $true
        break
    }
}

if (-not $HasDevListBucket) {
    throw (
        "Worker role '$WorkerRoleName' is missing s3:ListBucket on " +
        "'$ExpectedDevBucketArn'. Worker V2 uses HeadObject for immutable " +
        "generation-output existence checks; without ListBucket, an absent " +
        "object is returned as 403 instead of 404."
    )
}

Write-Host "Worker DEV ListBucket: PRESENT"

# ------------------------------------------------------------------
# 5. Docker availability
# ------------------------------------------------------------------

docker version --format '{{.Server.Version}}' | Out-Null
Assert-LastExitCode "Checking Docker"

docker buildx version | Out-Null
Assert-LastExitCode "Checking Docker Buildx"

Write-Host "Docker / Buildx    : READY"

Write-Host ""
Write-Host "PRE-FLIGHT PASSED"
Write-Host ""

if ($PreflightOnly) {
    Write-Host "PreflightOnly requested. No image was built or pushed."
    exit 0
}

# ------------------------------------------------------------------
# 5. ECR authentication
# ------------------------------------------------------------------

$EcrPassword = aws ecr get-login-password --region $Region
Assert-LastExitCode "Getting ECR login password"

$EcrPassword |
    docker login `
        --username AWS `
        --password-stdin $Registry

Assert-LastExitCode "Logging Docker into ECR"

# ------------------------------------------------------------------
# 6. Build one Lambda-compatible AMD64 image
# ------------------------------------------------------------------

$ReleaseImage = "${EcrUri}:$ReleaseTag"
$GitImage     = "${EcrUri}:$GitTag"

Write-Host ""
Write-Host "Building:"
Write-Host "  $ReleaseImage"
Write-Host "  $GitImage"
Write-Host ""

docker buildx build `
    --platform linux/amd64 `
    --provenance=false `
    --load `
    --tag $ReleaseImage `
    --tag $GitImage `
    .

Assert-LastExitCode "Building Lambda image"

$Platform = (
    docker image inspect `
        $ReleaseImage `
        --format '{{.Os}}/{{.Architecture}}'
).Trim()

Assert-LastExitCode "Inspecting built image"

if ($Platform -ne "linux/amd64") {
    throw "Wrong image platform '$Platform'. Expected linux/amd64."
}

Write-Host "Image platform      : $Platform"

# ------------------------------------------------------------------
# 7. Push both immutable identities
# ------------------------------------------------------------------

docker push $ReleaseImage
Assert-LastExitCode "Pushing $ReleaseTag"

docker push $GitImage
Assert-LastExitCode "Pushing $GitTag"

# ------------------------------------------------------------------
# 8. Resolve and verify ECR digest
# ------------------------------------------------------------------

$ReleaseDigest = (
    aws ecr describe-images `
        --repository-name $Repository `
        --image-ids "imageTag=$ReleaseTag" `
        --region $Region `
        --query 'imageDetails[0].imageDigest' `
        --output text
).Trim()

Assert-LastExitCode "Resolving release digest"

$GitDigest = (
    aws ecr describe-images `
        --repository-name $Repository `
        --image-ids "imageTag=$GitTag" `
        --region $Region `
        --query 'imageDetails[0].imageDigest' `
        --output text
).Trim()

Assert-LastExitCode "Resolving Git-tag digest"

if ($ReleaseDigest -ne $GitDigest) {
    throw "ECR digest mismatch. $ReleaseTag=$ReleaseDigest but $GitTag=$GitDigest"
}

$ManifestType = (
    aws ecr describe-images `
        --repository-name $Repository `
        --image-ids "imageTag=$ReleaseTag" `
        --region $Region `
        --query 'imageDetails[0].imageManifestMediaType' `
        --output text
).Trim()

Assert-LastExitCode "Reading ECR manifest type"

$ExpectedManifestType = "application/vnd.docker.distribution.manifest.v2+json"

if ($ManifestType -ne $ExpectedManifestType) {
    throw "Unexpected ECR manifest type '$ManifestType'. Expected '$ExpectedManifestType'."
}

Write-Host ""
Write-Host "ECR DIGEST VERIFIED"
Write-Host "Release digest     : $ReleaseDigest"
Write-Host "Git digest         : $GitDigest"
Write-Host "Manifest type      : $ManifestType"

# ------------------------------------------------------------------
# 9. Optimistic concurrency protection
# ------------------------------------------------------------------

$BeforeRevision = (
    aws lambda get-function-configuration `
        --function-name $Function `
        --region $Region `
        --query RevisionId `
        --output text
).Trim()

Assert-LastExitCode "Reading Lambda RevisionId"

$DigestImageUri = "$EcrUri@$ReleaseDigest"

Write-Host ""
Write-Host "Deploying immutable image:"
Write-Host "  $DigestImageUri"
Write-Host "Expected RevisionId:"
Write-Host "  $BeforeRevision"
Write-Host ""

# ------------------------------------------------------------------
# 10. Deploy BY DIGEST, never by tag
# ------------------------------------------------------------------

aws lambda update-function-code `
    --function-name $Function `
    --image-uri $DigestImageUri `
    --revision-id $BeforeRevision `
    --region $Region `
    --output json |
    Out-Null

Assert-LastExitCode "Updating Lambda image"

aws lambda wait function-updated-v2 `
    --function-name $Function `
    --region $Region

Assert-LastExitCode "Waiting for Lambda deployment"

# ------------------------------------------------------------------
# 11. Post-deploy proof
# ------------------------------------------------------------------

$Live = aws lambda get-function `
    --function-name $Function `
    --region $Region `
    --output json |
    ConvertFrom-Json

Assert-LastExitCode "Reading deployed Lambda"

$ResolvedImageUri = $Live.Code.ResolvedImageUri
$AfterRevision    = $Live.Configuration.RevisionId
$UpdateStatus     = $Live.Configuration.LastUpdateStatus

if ($UpdateStatus -ne "Successful") {
    throw "Lambda deployment status is '$UpdateStatus', not Successful."
}

$ResolvedDigest = ($ResolvedImageUri -split '@')[-1]

if ($ResolvedDigest -ne $ReleaseDigest) {
    throw "Lambda digest mismatch. Expected $ReleaseDigest but Lambda resolved $ResolvedDigest"
}

Write-Host ""
Write-Host "======================================================"
Write-Host "DEPLOYMENT VERIFIED"
Write-Host "======================================================"
Write-Host "Git SHA            : $GitSha"
Write-Host "Release tag        : $ReleaseTag"
Write-Host "Git tag            : $GitTag"
Write-Host "Image digest       : $ReleaseDigest"
Write-Host "Lambda image       : $ResolvedImageUri"
Write-Host "Previous revision  : $BeforeRevision"
Write-Host "Current revision   : $AfterRevision"
Write-Host "Update status      : $UpdateStatus"
Write-Host "======================================================"