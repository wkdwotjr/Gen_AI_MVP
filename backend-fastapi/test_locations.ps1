# ============================================================
# POST /api/v1/locations  -  TC-L1 ~ TC-L10
# Run: cd backend-fastapi ; .\test_locations.ps1
#   (Cloud SQL Proxy + uvicorn must be running)
# ASCII-only on purpose: PowerShell 5.1 reads BOM-less .ps1 as CP949.
# ============================================================
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$BASE = "http://127.0.0.1:8000/api/v1"
$TMP  = Join-Path $env:TEMP "couponkok_loc_body.json"

# --- tune these ------------------------------------------------
$AJOU_LAT  = 37.2803       # Ajou univ. - megamgc 207m / starbucks 143m
$AJOU_LNG  = 127.0433
$FAR_LAT  = 37.267470     # 컴포즈커피 수원매교점 - 300m 내 보유 브랜드 없음
$FAR_LNG  = 127.017013
$BUSAN_LAT = 35.179600
$BUSAN_LNG = 129.075600
# ---------------------------------------------------------------

function P {
    param(
        [double]$Lat,
        [double]$Lng,
        [double]$Acc = 12.5,
        [int]$AgoSec = 0,
        [string]$Src = "PERIODIC"
    )
    @{
        lat         = $Lat
        lng         = $Lng
        accuracy_m  = $Acc
        recorded_at = (Get-Date).ToUniversalTime().AddSeconds(-$AgoSec).ToString("yyyy-MM-ddTHH:mm:ssZ")
        source      = $Src
    }
}

function Reset-Loc {
    Write-Host "  (reset users.last_location)" -ForegroundColor DarkGray
    python scripts/reset_last_location.py | Out-Null
}

function Send-Loc {
    param(
        [object[]]$Points,
        [string]$Title,
        [string]$Expect
    )

    if ($Points -eq $null -or $Points.Count -eq 0) {
        $json = '{"points":[]}'
    } else {
        $json = @{ points = [object[]]$Points } | ConvertTo-Json -Depth 6 -Compress
    }
    [IO.File]::WriteAllText($TMP, $json, (New-Object Text.UTF8Encoding($false)))

    Write-Host ""
    Write-Host ("-" * 72) -ForegroundColor DarkGray
    Write-Host $Title -ForegroundColor Cyan
    Write-Host ("  expect: " + $Expect) -ForegroundColor DarkYellow

    $out = (curl.exe -s -w "`n__STATUS__%{http_code}" -X POST "$BASE/locations" -H "Content-Type: application/json" --data-binary "@$TMP") -join "`n"
    $parts  = $out -split "__STATUS__"
    $body   = $parts[0]
    $status = ($parts[1]).Trim()

    Write-Host ("  HTTP " + $status)
    try   { $body | ConvertFrom-Json | ConvertTo-Json -Depth 8 }
    catch { Write-Host $body }
}

Write-Host "=== POST /api/v1/locations : start ===" -ForegroundColor Green

# TC-L1  normal match
Reset-Loc
Send-Loc -Title "TC-L1  normal point (Ajou)" -Expect "200 / accepted=1 rejected=0 / matches=2 (143m, 207m) / briefing=TEMPLATE" -Points @( (P $AJOU_LAT $AJOU_LNG) )

# TC-L2  low accuracy
Send-Loc -Title "TC-L2  accuracy_m = 150" -Expect "200 / accepted=0 rejected=1 / LOW_ACCURACY / matches=[]" -Points @( (P $AJOU_LAT $AJOU_LNG -Acc 150) )

# TC-L3  stale
Send-Loc -Title "TC-L3  recorded_at 15 min ago" -Expect "200 / accepted=0 rejected=1 / STALE / matches=[]" -Points @( (P $AJOU_LAT $AJOU_LNG -AgoSec 900) )

# TC-L4  50m duplicate suppression
Write-Host ""
Write-Host ">>> before TC-L4: run 'python scripts/check_f02.py' in another window, note last_location_at" -ForegroundColor Magenta
Read-Host "    press Enter when noted"

Send-Loc -Title "TC-L4  ~30m away (lat +0.00027)" -Expect "200 / accepted=1 / matches ok / last_location_at UNCHANGED" -Points @( (P ($AJOU_LAT + 0.00027) $AJOU_LNG) )

Write-Host ">>> after TC-L4: run check_f02.py again, last_location_at must be identical" -ForegroundColor Magenta
Read-Host "    press Enter when checked"

# TC-L5  store nearby but no coupon for that brand
Reset-Loc
Send-Loc -Title "TC-L5  no matching brand (Gwanggyo)" -Expect "200 / accepted=1 / matches=[] (not an error)" -Points @( (P $FAR_LAT $FAR_LNG) )

# TC-L6  out of range latitude -> whole request rejected
Send-Loc -Title "TC-L6  second point lat = 91.0" -Expect "422 / INVALID_COORDINATE / detail.index=1" -Points @( (P $AJOU_LAT $AJOU_LNG), (P 91.0 127.0) )

# TC-L7  implausible speed
Send-Loc -Title "TC-L7  jump to Busan as PERIODIC" -Expect "200 / accepted=0 / IMPLAUSIBLE_SPEED / matches=[]" -Points @( (P $BUSAN_LAT $BUSAN_LNG) )

# TC-L8  same jump but MANUAL_REFRESH (C-15)
Send-Loc -Title "TC-L8  same jump as MANUAL_REFRESH" -Expect "200 / accepted=1 / no IMPLAUSIBLE_SPEED / matches=[]" -Points @( (P $BUSAN_LAT $BUSAN_LNG -Src "MANUAL_REFRESH") )

# TC-L9  too many points
$many = 1..51 | ForEach-Object { P $AJOU_LAT $AJOU_LNG }
Send-Loc -Title "TC-L9  51 points" -Expect "422 / TOO_MANY_POINTS / detail received=51 max=50" -Points $many

# TC-L10  empty array
Send-Loc -Title "TC-L10 points = []" -Expect "422 / VALIDATION_ERROR" -Points @()

Write-Host ""
Write-Host "=== done ===" -ForegroundColor Green