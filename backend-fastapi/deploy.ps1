# =====================================================================
#  couponkok - Cloud Run deploy script
#  ASCII ONLY. Do not add Korean comments to this file.
#  (PowerShell 5.1 reads BOM-less scripts as CP949; UTF-8 Korean bytes
#   swallow the following quote/paren and break the parser elsewhere.)
#
#  Run from: backend-fastapi\
#
#    .\deploy.ps1 -Bootstrap        # first time only: APIs, repo, SA, secret
#    .\deploy.ps1                   # build + deploy
#    .\deploy.ps1 -SkipBuild        # redeploy with env changes only (fast)
#    .\deploy.ps1 -SyncSecret       # push DB_PASS from local .env to Secret Manager
# =====================================================================

[CmdletBinding()]
param(
    [string] $ProjectId          = "proj-aj06-211200020328",
    [string] $Region             = "asia-northeast3",
    [string] $Service            = "couponkok-api",
    [string] $InstanceConn       = "proj-aj06-211200020328:asia-northeast3:couponkok-db",
    [string] $RepoName           = "couponkok",
    [string] $ImageName          = "api",
    [string] $Tag                = "",
    [string] $ServiceAccountName = "couponkok-run",
    [string] $DbSecretName       = "couponkok-db-pass",
    [string] $EnvFile            = ".env",
    [int]    $MinInstances       = 1,
    [int]    $MaxInstances       = 3,
    [switch] $Bootstrap,
    [switch] $SkipBuild,
    [switch] $SyncSecret
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------
# Runtime environment variables pushed to Cloud Run.
# KEY NAMES MUST MATCH WHAT app/core.py READS FROM .env.
# Check with:  Select-String -Path app\core.py -Pattern "getenv|environ"
#
# Do NOT set DB_HOST / DB_PORT here: their absence is what makes the code
# take the INSTANCE_CONN unix-socket branch (/cloudsql/<conn>).
# DB_PASS is injected separately from Secret Manager, never as plain env.
# ---------------------------------------------------------------------
$EnvVars = [ordered]@{
    "AUTH_DISABLED"        = "true"     # documented exception - see 01_API_SPEC v0.5 section 1.2
    "INSTANCE_CONN"        = $InstanceConn
    "DB_NAME"              = "couponkok"
    "DB_USER"              = "couponkok_app"
    "GCP_PROJECT_ID"       = $ProjectId
    "GEMINI_AUTH_MODE"     = "vertex"
    "GCP_LOCATION"         = "us-central1"
    "SEARCH_RADIUS_M"      = "300"
}

# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------
function Write-Step([string] $Text) {
    Write-Host ""
    Write-Host ("=== " + $Text) -ForegroundColor Cyan
}

function Write-Warn([string] $Text) {
    Write-Host ("[warn] " + $Text) -ForegroundColor Yellow
}

function Invoke-Gcloud {
    param(
        [string[]] $GcloudArgs,
        [switch]   $AllowFailure
    )
    Write-Host ("  gcloud " + ($GcloudArgs -join " ")) -ForegroundColor DarkGray
    # Local Continue: PS 5.1 wraps each merged stderr line as a terminating
    # NativeCommandError under the script's global "Stop" preference, even
    # when gcloud's own exit code is 0 (e.g. informational notices on stderr).
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & gcloud @GcloudArgs 2>&1
    $ErrorActionPreference = $prevEap
    $code = $LASTEXITCODE
    if ($code -ne 0 -and -not $AllowFailure) {
        Write-Host ($out | Out-String) -ForegroundColor Red
        throw ("gcloud failed with exit code " + $code)
    }
    return ,@($out, $code)
}

function Get-GcloudValue {
    # stdout only. Do NOT merge stderr here: gcloud writes progress lines to
    # stderr and they would end up concatenated into the captured URL/digest.
    param([string[]] $GcloudArgs)
    Write-Host ("  gcloud " + ($GcloudArgs -join " ")) -ForegroundColor DarkGray
    $out = & gcloud @GcloudArgs
    if ($LASTEXITCODE -ne 0) { throw ("gcloud failed with exit code " + $LASTEXITCODE) }
    return (($out | Out-String).Trim())
}

function Test-GcloudResource {
    param([string[]] $GcloudArgs)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $null = & gcloud @GcloudArgs 2>&1
    $ErrorActionPreference = $prevEap
    return ($LASTEXITCODE -eq 0)
}

function Get-DotEnvValue {
    param([string] $Path, [string] $Key)
    if (-not (Test-Path $Path)) { return $null }
    foreach ($line in (Get-Content -Path $Path)) {
        $t = $line.Trim()
        if ($t -eq "" -or $t.StartsWith("#")) { continue }
        $i = $t.IndexOf("=")
        if ($i -lt 1) { continue }
        if ($t.Substring(0, $i).Trim() -ne $Key) { continue }
        $v = $t.Substring($i + 1).Trim()
        if ($v.Length -ge 2) {
            $q = $v.Substring(0, 1)
            if (($q -eq '"' -or $q -eq "'") -and $v.EndsWith($q)) {
                $v = $v.Substring(1, $v.Length - 2)
            }
        }
        return $v
    }
    return $null
}

function Set-DbSecret {
    param([string] $SecretName, [string] $Value)
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        # WriteAllText, not Set-Content: Set-Content appends a newline that
        # becomes part of the password and produces an auth failure at runtime.
        $enc = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($tmp, $Value, $enc)
        Invoke-Gcloud @("secrets", "versions", "add", $SecretName,
                        "--data-file=$tmp", "--project", $ProjectId) | Out-Null
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------
# 0. preflight
# ---------------------------------------------------------------------
Write-Step "Preflight"

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "gcloud not found on PATH. Install Google Cloud CLI and reopen PowerShell."
}
foreach ($f in @("Dockerfile", "requirements.txt", "app", "static", "config")) {
    if (-not (Test-Path $f)) {
        throw ("Not found: " + $f + ". Run this script from the backend-fastapi directory.")
    }
}
if (Test-Path ".dockerignore") {
    $di = Get-Content ".dockerignore" -Raw
    if ($di -notmatch "(?m)^\.env\s*$") {
        Write-Warn ".dockerignore does not exclude .env - secrets may be baked into the image."
    }
} else {
    Write-Warn ".dockerignore missing. .env and .venv will be uploaded to Cloud Build."
}

if ($Tag -eq "") { $Tag = Get-Date -Format "yyyyMMdd-HHmmss" }

$ServiceAccount = $ServiceAccountName + "@" + $ProjectId + ".iam.gserviceaccount.com"
$ImageBase      = $Region + "-docker.pkg.dev/" + $ProjectId + "/" + $RepoName + "/" + $ImageName
$Image          = $ImageBase + ":" + $Tag

Write-Host ("  project  : " + $ProjectId)
Write-Host ("  region   : " + $Region)
Write-Host ("  service  : " + $Service)
Write-Host ("  image    : " + $Image)
Write-Host ("  sql      : " + $InstanceConn)
Write-Host ("  sa       : " + $ServiceAccount)

Invoke-Gcloud @("config", "set", "project", $ProjectId) | Out-Null

# ---------------------------------------------------------------------
# 1. bootstrap (first run only)
# ---------------------------------------------------------------------
if ($Bootstrap) {
    Write-Step "Bootstrap: enabling APIs (takes 1-2 minutes)"
    Invoke-Gcloud @("services", "enable",
        "run.googleapis.com",
        "cloudbuild.googleapis.com",
        "artifactregistry.googleapis.com",
        "sqladmin.googleapis.com",
        "aiplatform.googleapis.com",
        "secretmanager.googleapis.com",
        "--project", $ProjectId) | Out-Null

    Write-Step "Bootstrap: Artifact Registry repository"
    if (Test-GcloudResource @("artifacts", "repositories", "describe", $RepoName,
                              "--location", $Region, "--project", $ProjectId)) {
        Write-Host "  repository already exists - skipping"
    } else {
        Invoke-Gcloud @("artifacts", "repositories", "create", $RepoName,
            "--repository-format", "docker",
            "--location", $Region,
            "--description", "couponkok MVP images",
            "--project", $ProjectId) | Out-Null
    }

    Write-Step "Bootstrap: runtime service account"
    if (Test-GcloudResource @("iam", "service-accounts", "describe", $ServiceAccount,
                              "--project", $ProjectId)) {
        Write-Host "  service account already exists - skipping"
    } else {
        Invoke-Gcloud @("iam", "service-accounts", "create", $ServiceAccountName,
            "--display-name", "couponkok Cloud Run runtime",
            "--project", $ProjectId) | Out-Null
        Start-Sleep -Seconds 10
    }

    # cloudsql.client  -> unix socket connection to Cloud SQL
    # aiplatform.user  -> Vertex AI Gemini calls via ADC (replaces local user ADC)
    # secretmanager.secretAccessor -> DB_PASS injection
    Write-Step "Bootstrap: IAM roles"
    foreach ($role in @("roles/cloudsql.client", "roles/aiplatform.user", "roles/secretmanager.secretAccessor")) {
        $bound = $false
        for ($attempt = 1; $attempt -le 5; $attempt++) {
            $result = Invoke-Gcloud @("projects", "add-iam-policy-binding", $ProjectId,
                "--member", ("serviceAccount:" + $ServiceAccount),
                "--role", $role,
                "--condition", "None",
                "--quiet") -AllowFailure
            if ($result[1] -eq 0) { $bound = $true; break }
            # A freshly created service account can take a few seconds to
            # propagate through IAM before it can be bound to a role.
            Write-Host ("  service account not yet visible to IAM, retrying (" + $attempt + "/5)...")
            Start-Sleep -Seconds 5
        }
        if (-not $bound) { throw ("Could not bind " + $role + " to " + $ServiceAccount + " after retries.") }
    }

    Write-Step "Bootstrap: Secret Manager entry for DB_PASS"
    if (-not (Test-GcloudResource @("secrets", "describe", $DbSecretName, "--project", $ProjectId))) {
        Invoke-Gcloud @("secrets", "create", $DbSecretName,
            "--replication-policy", "automatic",
            "--project", $ProjectId) | Out-Null
    }
    $SyncSecret = $true
}

# ---------------------------------------------------------------------
# 2. secret sync
# ---------------------------------------------------------------------
if ($SyncSecret) {
    Write-Step "Pushing DB password from local .env to Secret Manager"
    # DB_PASSWORD_APP is the password for the Cloud Run runtime DB user (see
    # $EnvVars DB_USER above, e.g. couponkok_app). It is NOT the same account
    # as local dev's DB_USER/DB_PASSWORD (typically postgres, used only via
    # Cloud SQL Auth Proxy on 127.0.0.1). Falling back to DB_PASSWORD here
    # would silently push the wrong user's password to the runtime secret.
    $dbPass = Get-DotEnvValue -Path $EnvFile -Key "DB_PASSWORD_APP"
    if ($null -eq $dbPass -or $dbPass -eq "") {
        $dbPass = Get-DotEnvValue -Path $EnvFile -Key "DB_PASS"
    }
    if ($null -eq $dbPass -or $dbPass -eq "") {
        $dbPass = Get-DotEnvValue -Path $EnvFile -Key "DB_PASSWORD"
        if ($dbPass) {
            Write-Warn "Using DB_PASSWORD (local dev user), not DB_PASSWORD_APP. Confirm this is really the runtime DB_USER's password."
        }
    }
    if ($null -eq $dbPass -or $dbPass -eq "") {
        throw ("No DB password found in " + $EnvFile + " (checked DB_PASSWORD_APP, DB_PASS, DB_PASSWORD). Add one, or create the secret version manually.")
    }
    Set-DbSecret -SecretName $DbSecretName -Value $dbPass
    Write-Host "  new secret version added (value not printed)"
}

# ---------------------------------------------------------------------
# 3. build
# ---------------------------------------------------------------------
if ($SkipBuild) {
    Write-Step "Skipping build - resolving latest pushed image"
    $digest = Get-GcloudValue @("artifacts", "docker", "images", "list", $ImageBase,
        "--sort-by", "~UPDATE_TIME", "--limit", "1",
        "--format", "value(version)", "--project", $ProjectId)
    if ($digest -eq "") { throw "No existing image found. Run without -SkipBuild." }
    $Image = $ImageBase + "@" + $digest
    Write-Host ("  reusing " + $Image)
} else {
    Write-Step "Cloud Build (3-6 minutes on first run)"
    Invoke-Gcloud @("builds", "submit",
        "--tag", $Image,
        "--project", $ProjectId,
        ".") | Out-Null
}

# ---------------------------------------------------------------------
# 4. deploy
# ---------------------------------------------------------------------
Write-Step "Deploying to Cloud Run"

$pairs = @()
foreach ($k in $EnvVars.Keys) { $pairs += ($k + "=" + $EnvVars[$k]) }
$envString = ($pairs -join ",")   # safe: no value here contains a comma

# --no-cpu-throttling is mandatory. Without it Cloud Run reclaims CPU after the
# 202 response and the BackgroundTasks Gemini parse (9-11s) is frozen mid-flight:
# the coupon stays PROCESSING forever and the client polls out at 30s.
$deployArgs = @(
    "run", "deploy", $Service,
    "--project", $ProjectId,
    "--region", $Region,
    "--platform", "managed",
    "--image", $Image,
    "--service-account", $ServiceAccount,
    "--allow-unauthenticated",
    "--no-cpu-throttling",
    "--cpu", "1",
    "--memory", "1Gi",
    "--timeout", "300",
    "--concurrency", "40",
    "--min-instances", "$MinInstances",
    "--max-instances", "$MaxInstances",
    "--port", "8080",
    "--add-cloudsql-instances", $InstanceConn,
    "--set-env-vars", $envString,
    "--set-secrets", ("DB_PASSWORD=" + $DbSecretName + ":latest"),
    "--quiet"
)
Invoke-Gcloud $deployArgs | Out-Null

# ---------------------------------------------------------------------
# 5. verify
# ---------------------------------------------------------------------
Write-Step "Verifying"

$ServiceUrl = Get-GcloudValue @("run", "services", "describe", $Service,
    "--project", $ProjectId, "--region", $Region,
    "--format", "value(status.url)")

if ($ServiceUrl -eq "") { throw "Could not resolve service URL." }

$healthOk = $false
for ($i = 1; $i -le 6; $i++) {
    try {
        $h = Invoke-RestMethod -Uri ($ServiceUrl + "/health") -Method Get -TimeoutSec 20
        Write-Host ("  /health -> status=" + $h.status + " db=" + $h.db + " version=" + $h.version) -ForegroundColor Green
        if ($h.status -eq "ok" -and $h.db -eq "ok") { $healthOk = $true }
        break
    } catch {
        Write-Host ("  attempt " + $i + "/6 failed, retrying in 5s...")
        Start-Sleep -Seconds 5
    }
}

Write-Host ""
Write-Host "---------------------------------------------------------------"
Write-Host ("SERVICE URL : " + $ServiceUrl) -ForegroundColor Green
Write-Host ("REVISION    : " + $Tag)
Write-Host "---------------------------------------------------------------"

if (-not $healthOk) {
    Write-Warn "/health did not return ok. Check logs:"
    Write-Host ("  gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=" + $Service + "' --limit 60 --project " + $ProjectId)
    Write-Warn "db=error usually means the DB_PASS secret, DB_USER, or --add-cloudsql-instances is wrong."
}

Write-Host ""
Write-Host "MANUAL STEP - Kakao Developers (map will not render until this is done):" -ForegroundColor Yellow
Write-Host "  [App settings] > [App] > [Platform keys] > JavaScript key > 'JavaScript SDK domain'"
Write-Host ("  Add exactly (no trailing slash): " + $ServiceUrl)
Write-Host "  Keep http://localhost:8000 registered as well for local runs."
Write-Host ""
Write-Host "Then update 01_API_SPEC section 1.1 Base URL (Prod) with the URL above."
Write-Host ""
Write-Host "Smoke test:" -ForegroundColor Cyan
Write-Host ("  Invoke-RestMethod " + $ServiceUrl + "/health")
Write-Host ("  Start-Process " + $ServiceUrl + "/")
Write-Host ("  Start-Process " + $ServiceUrl + "/docs")