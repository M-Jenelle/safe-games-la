# Deploy Safe Games LA to Cloud Run.
# Requires: gcloud auth login, and the crime CSVs present under data/.
# Secrets stay in the local .env and are sent as service env vars. They are not printed.

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

$project = "uc4-predictive-crime-intel"
$region = "us-west1"
$service = "safe-games-la"
$needed = @(
    "data\Crime_Data_from_2020_to_2024.csv",
    "data\raw\nibrs\nibrs_current.csv",
    "data\processed\venue_summary.json",
    "data\processed\crime_merged.json"
)
foreach ($path in $needed) {
    if (-not (Test-Path $path)) {
        throw "Missing $path. The heatmap and venue pages need this file in the container."
    }
}

$wanted = @(
    "GOOGLE_MAPS_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
    "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GEMINI_MODEL"
)
$pairs = @{}
if (Test-Path ".env") {
    foreach ($line in Get-Content ".env") {
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith("#") -or -not $text.Contains("=")) { continue }
        $name, $value = $text.Split("=", 2)
        $name = $name.Trim()
        if ($wanted -contains $name -and $value.Trim()) {
            $pairs[$name] = $value.Trim().Trim('"').Trim("'")
        }
    }
}
if (-not $pairs.ContainsKey("GOOGLE_MAPS_API_KEY")) {
    throw "GOOGLE_MAPS_API_KEY is missing from .env. The map cannot load without it."
}
if (-not $pairs.ContainsKey("GOOGLE_CLOUD_PROJECT")) { $pairs["GOOGLE_CLOUD_PROJECT"] = "uc4-predictive-crime-intel" }
if (-not $pairs.ContainsKey("GOOGLE_CLOUD_LOCATION")) { $pairs["GOOGLE_CLOUD_LOCATION"] = "us-west1" }
if (-not $pairs.ContainsKey("GEMINI_MODEL")) { $pairs["GEMINI_MODEL"] = "gemini-2.5-flash" }

$envFile = Join-Path $env:TEMP "safe-games-la-run-env.yaml"
$yaml = foreach ($name in $pairs.Keys) {
    $escaped = $pairs[$name].Replace("'", "''")
    "${name}: '$escaped'"
}
Set-Content -Path $envFile -Value ($yaml -join "`n") -Encoding ascii

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project $project
if ($LASTEXITCODE -ne 0) { throw "Could not enable Cloud Run APIs." }
try {
    gcloud run deploy $service `
        --source . `
        --project $project `
        --region $region `
        --allow-unauthenticated `
        --memory 8Gi `
        --cpu 2 `
        --timeout 600 `
        --concurrency 8 `
        --max-instances 2 `
        --env-vars-file $envFile
    if ($LASTEXITCODE -ne 0) { throw "Cloud Run deploy failed." }
} finally {
    if (Test-Path $envFile) { Remove-Item $envFile -Force }
}

$url = gcloud run services describe $service --project $project --region $region --format="value(status.url)"
Write-Output "Deployed: $url"
Write-Output "Add that URL, and ${url}/* , to the Maps key's allowed referrers or the basemap stays blank."
