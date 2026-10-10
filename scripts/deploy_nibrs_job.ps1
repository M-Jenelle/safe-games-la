# Create the Tuesday NIBRS check on Cloud Scheduler.
# Run scripts/deploy_cloud_run.ps1 first so the service image can load a new extract.
# Secrets stay in the local .env and are sent as job env vars. They are not printed.

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

function Invoke-GcloudRetry {
    param(
        [scriptblock]$Action,
        [string]$Failure
    )
    for ($try = 1; $try -le 8; $try++) {
        & $Action
        if ($LASTEXITCODE -eq 0) { return }
        if ($try -eq 8) { throw $Failure }
        Start-Sleep -Seconds 8
    }
}

function Test-Gcloud {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GcloudArgs)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & gcloud @GcloudArgs 2>&1 | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    return $code
}

$project = "uc4-predictive-crime-intel"
$region = "us-west1"
$service = "safe-games-la"
$job = "nibrs-sync"
$bucket = "uc4-predictive-crime-intel-nibrs"
$jobAccount = "nibrs-sync@$project.iam.gserviceaccount.com"

gcloud services enable run.googleapis.com cloudscheduler.googleapis.com storage.googleapis.com --project $project
if ($LASTEXITCODE -ne 0) { throw "Could not enable the NIBRS job APIs." }

if ((Test-Gcloud storage buckets describe "gs://$bucket" --project $project) -ne 0) {
    gcloud storage buckets create "gs://$bucket" --project $project --location $region --uniform-bucket-level-access
    if ($LASTEXITCODE -ne 0) { throw "Could not create the NIBRS bucket." }
}

if ((Test-Gcloud iam service-accounts describe $jobAccount --project $project) -ne 0) {
    gcloud iam service-accounts create nibrs-sync --project $project --display-name "Safe Games LA NIBRS sync"
    if ($LASTEXITCODE -ne 0) { throw "Could not create the NIBRS job account." }
}

Invoke-GcloudRetry {
    gcloud storage buckets add-iam-policy-binding "gs://$bucket" `
        --project $project `
        --member "serviceAccount:$jobAccount" `
        --role "roles/storage.objectAdmin"
} "Could not let the job write the NIBRS bucket."

$runtime = gcloud run services describe $service --project $project --region $region --format="value(spec.template.spec.serviceAccountName)"
if ($LASTEXITCODE -ne 0) { throw "Could not read the site service account." }
if (-not $runtime) {
    $projectNumber = gcloud projects describe $project --format="value(projectNumber)"
    if ($LASTEXITCODE -ne 0 -or -not $projectNumber) { throw "Could not read the project number." }
    $runtime = "$projectNumber-compute@developer.gserviceaccount.com"
}
gcloud storage buckets add-iam-policy-binding "gs://$bucket" `
    --project $project `
    --member "serviceAccount:$runtime" `
    --role "roles/storage.objectViewer"
if ($LASTEXITCODE -ne 0) { throw "Could not let the site read the NIBRS bucket." }

gcloud projects add-iam-policy-binding $project `
    --member "serviceAccount:$jobAccount" `
    --role "roles/run.developer"
if ($LASTEXITCODE -ne 0) { throw "Could not let the job reload the site." }

$image = gcloud run services describe $service --project $project --region $region --format="value(spec.template.spec.containers[0].image)"
if ($LASTEXITCODE -ne 0 -or -not $image) { throw "Could not read the site image. Deploy the site first." }

$pairs = @{
    "NIBRS_BUCKET" = $bucket
    "NIBRS_ROLL_SERVICE" = $service
    "GOOGLE_CLOUD_PROJECT" = $project
    "GOOGLE_CLOUD_LOCATION" = $region
}
if (Test-Path ".env") {
    foreach ($line in Get-Content ".env") {
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith("#") -or -not $text.Contains("=")) { continue }
        $name, $value = $text.Split("=", 2)
        if ($name.Trim() -eq "SOCRATA_APP_TOKEN" -and $value.Trim()) {
            $pairs["SOCRATA_APP_TOKEN"] = $value.Trim().Trim('"').Trim("'")
        }
    }
}
$envFile = Join-Path $env:TEMP "safe-games-la-nibrs-env.yaml"
$yaml = foreach ($name in ($pairs.Keys | Sort-Object)) {
    $escaped = $pairs[$name].Replace("'", "''")
    "${name}: '$escaped'"
}
Set-Content -Path $envFile -Value ($yaml -join "`n") -Encoding ascii
try {
    gcloud run jobs deploy $job `
        --image $image `
        --project $project `
        --region $region `
        --service-account $jobAccount `
        --command python `
        '--args=-m,pipeline.nibrs' `
        --memory 8Gi `
        --cpu 2 `
        --task-timeout 3600 `
        --max-retries 1 `
        --env-vars-file $envFile
    if ($LASTEXITCODE -ne 0) { throw "Could not deploy the NIBRS job." }
} finally {
    if (Test-Path $envFile) { Remove-Item $envFile -Force }
}

gcloud run jobs add-iam-policy-binding $job `
    --project $project `
    --region $region `
    --member "serviceAccount:$jobAccount" `
    --role "roles/run.invoker"
if ($LASTEXITCODE -ne 0) { throw "Could not let Cloud Scheduler start the NIBRS job." }

$uri = "https://$region-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$project/jobs/${job}:run"
if ((Test-Gcloud scheduler jobs describe $job --project $project --location $region) -eq 0) {
    gcloud scheduler jobs update http $job `
        --project $project `
        --location $region `
        --schedule "0 18 * * 2" `
        --time-zone "America/Los_Angeles" `
        --uri $uri `
        --http-method POST `
        --oauth-service-account-email $jobAccount
} else {
    gcloud scheduler jobs create http $job `
        --project $project `
        --location $region `
        --schedule "0 18 * * 2" `
        --time-zone "America/Los_Angeles" `
        --uri $uri `
        --http-method POST `
        --oauth-service-account-email $jobAccount
}
if ($LASTEXITCODE -ne 0) { throw "Could not schedule the Tuesday NIBRS check." }

$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
schtasks /Delete /F /TN "Safe Games LA NIBRS sync" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Output "No local NIBRS task to remove." }
$ErrorActionPreference = $previous

Write-Output "NIBRS check: Tuesdays at 6:00 PM Pacific."
Write-Output "A new portal timestamp rebuilds the hosted counts. An unchanged Tuesday does nothing else."
