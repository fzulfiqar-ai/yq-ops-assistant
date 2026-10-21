# Market Intel v2 (releases R8m + R8n) - the release script. Prepared by Claude, 14-Oct-2026; the owner runs it.
#
# The runbook in plain words (what each part does, how long it takes, what can go wrong, the rollback and the
# after-release list) is docs\MARKET_INTEL_RELEASE.md. Read it once before the first part.
#
# Run it from the MAIN project folder, on branch main, after  git pull --ff-only  . It refuses to run from a
# code worktree (.claude\worktrees\...): a worktree finds the main folder's .env, which is production.
#
#   powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part check
#   powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part backup
#   powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part migrate
#   powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part api
#   powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part web
#   powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part import
#   powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part verify
# and only if something is wrong after the release:
#   powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part rollback
#
# Order: check -> backup -> migrate -> api -> web -> import -> verify. One part at a time; each part can be run
# again on its own. Before every step that writes to production (or runs on it) the script asks
#     Type YES to continue
# and anything else stops the part there (in the rollback: skips that piece). The first failure stops the part
# and says what failed. Everything shown on screen also goes to business_data\releases\<date>_mi_v2.log; anything
# that looks like a password, key or token is masked first, and no secret is ever printed.
# business_data\releases\mi_v2_state.json keeps what a later part needs: the encrypted backup (its archive), the
# gate snapshot and the API deploy that was live before the release (the rollback target).
#
# The backup is  python -m scripts.yq_backup release mi-v2  (a later attempt: mi-v2-2, mi-v2-3 ...): every table,
# checked, encrypted and read back under %LOCALAPPDATA%\yq-backups\release (never under business_data, never in
# OneDrive), the plain copy deleted; its last line is the archive. A table restore unpacks it outside OneDrive first
# (yq_backup unpack, whose last line is the folder it checked).
#
# It never pushes to GitHub, never changes a Render or Cloudflare setting, never deletes the load lock or a backup and
# never forces the r8m reverse (that switch is a decision for Claude and the owner, docs\MIGRATIONS.md, Release R8m).
#
# Exit codes: 0 done, 1 failed (stop and tell Claude), 2 stopped (no YES, or something to do first).

param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('check', 'backup', 'migrate', 'api', 'web', 'import', 'verify', 'rollback')]
  [string]$Part
)

# 'Continue': git and python print progress on stderr; every step is checked by its exit code instead
$ErrorActionPreference = 'Continue'
# the progress bar of Invoke-WebRequest slows Windows PowerShell 5.1 down a lot
$ProgressPreference = 'SilentlyContinue'
# git must never stop on a question (OneDrive-locked folders, a password prompt) or start its clean-up midway
$env:GIT_ASK_YESNO = 'false'
$env:GIT_TERMINAL_PROMPT = '0'
$env:GIT_CONFIG_COUNT = '2'
$env:GIT_CONFIG_KEY_0 = 'maintenance.auto'; $env:GIT_CONFIG_VALUE_0 = 'false'
$env:GIT_CONFIG_KEY_1 = 'gc.auto';          $env:GIT_CONFIG_VALUE_1 = '0'
# python: UTF-8 and unbuffered, so its lines appear (and are logged) as they happen
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
try { $utf8 = New-Object System.Text.UTF8Encoding($false); [Console]::OutputEncoding = $utf8 } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

$ROOT    = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$REPO    = 'fzulfiqar-ai/yq-ops-assistant'     # the GitHub repository: CI is read without a key while it is public, with GH_READ_TOKEN once it is private
$API     = 'https://api.yqmarketplace.com'
$MARKET  = 'https://yqmarketplace.com'
$PORTAL  = 'https://ops.yqmarketplace.com'
$UA      = 'yq-release'
$DAY     = Get-Date -Format 'yyyy-MM-dd'
$RELDIR  = Join-Path $ROOT 'business_data\releases'
$LOG     = Join-Path $RELDIR ($DAY + '_mi_v2.log')
$STATE   = Join-Path $RELDIR 'mi_v2_state.json'
$ENVFILE = Join-Path $ROOT '.env'
$LOCK    = Join-Path $ROOT 'business_data\ops\LOCK'
$GATES   = Join-Path $ROOT 'business_data\gates'
# The two migrations, applied in this order: r8m (needs r7b_market_intel, live since 28-Sep-2026), then r8n
# (needs r7b_market_intel and r7b_ai_head, live since R7d). r8n reads no r8m object, so either order would
# work; r8m goes first as docs\MIGRATIONS.md lists it, and the rollback reverses r8n first, then r8m.
$R8M     = 'scripts\r8m_market_quality_migration.sql'
$R8N     = 'scripts\r8n_ai_market_signals_migration.sql'
$R8N_REV = 'scripts\r8n_ai_market_signals_reverse.sql'
$R8M_REV = 'scripts\r8m_market_quality_reverse.sql'

$script:HEAD = ''
$script:GitCode = 0
$script:LastOutput = @()
$script:Secrets = @()
$script:GhHeaders = $null

#region helpers
function Hide-Secret([string]$s) {
  # Masks anything that looks like a secret before it reaches the screen or the log.
  if (-not $s) { return '' }
  foreach ($v in $script:Secrets) { if ($v -and $v.Length -ge 8) { $s = $s.Replace($v, '***') } }
  $s = $s -replace '(?i)(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@', '$1***@'
  $s = $s -replace 'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}', '[token hidden]'
  $s = $s -replace '\brnd_[A-Za-z0-9]{8,}', 'rnd_***'
  $s = $s -replace '\bsb_(secret|publishable)_[A-Za-z0-9_-]{8,}', 'sb_${1}_***'
  $s = $s -replace '\bgh[pousr]_[A-Za-z0-9]{16,}', 'gh_***'
  $s = $s -replace '\bsk-[A-Za-z0-9_-]{16,}', 'sk-***'
  $s = $s -replace '(?i)\b([a-z0-9_]*(?:api[_-]?key|[a-z0-9]+_key|token|secret|password|passwd|authorization)[a-z0-9_]*)(\s*[=:]\s*)(bearer\s+)?[^\s,;]{6,}', '$1$2***'
  return $s
}

function Add-LogLine([string]$text) {
  $line = (Get-Date -Format 'HH:mm:ss') + '  ' + (Hide-Secret $text)
  for ($i = 0; $i -lt 3; $i++) {
    try { Add-Content -LiteralPath $LOG -Value $line -Encoding UTF8 -ErrorAction Stop; return } catch { Start-Sleep -Milliseconds 300 }
  }
  Write-Host '  (a line could not be written to the log: OneDrive may be holding the file)' -ForegroundColor DarkYellow
}

function Say([string]$text, [string]$color) {
  $t = Hide-Secret $text
  if ($color) { Write-Host $t -ForegroundColor $color } else { Write-Host $t }
  Add-LogLine $t
}

function Step([string]$title) {
  Say ''
  Say ('=== ' + $title) Cyan
}

function Stop-Part([int]$code, [string]$why) {
  if ($code -eq 0) { Say $why Green } elseif ($code -eq 2) { Say $why Yellow } else { Say $why Red }
  Add-LogLine ('==== part ' + $Part + ' ended, exit code ' + $code)
  exit $code
}

function Fail([string]$what) {
  Stop-Part 1 ('FAILED: ' + $what + '. Stop here and tell Claude (log: ' + $LOG + ').')
}

function Ask-Yes([string]$what) {
  # The one question before a production step. True only when the owner typed YES.
  Say ''
  Say ('PRODUCTION STEP: ' + $what) Yellow
  $a = Read-Host 'Type YES to continue'
  $ok = ([string]$a).Trim() -eq 'YES'
  if ($ok) { Add-LogLine 'answer: YES' } else { Add-LogLine 'answer: not YES' }
  return $ok
}

function Confirm-Step([string]$what) {
  # Forward parts: anything but YES stops the part before the step (nothing of it has run).
  if (-not (Ask-Yes $what)) { Stop-Part 2 ('Stopped before this step, as you answered; nothing of it ran: ' + $what) }
}

function Invoke-Native([string]$exe, [string[]]$argv, [string]$where) {
  # Runs one program, shows and logs its output line by line (secrets masked), returns its exit code.
  if (-not $where) { $where = $ROOT }
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { Fail ($exe + ' was not found on this PC') }
  Add-LogLine ('RUN  ' + $exe + ' ' + ($argv -join ' '))
  $script:LastOutput = @()
  $global:LASTEXITCODE = 0
  Push-Location -LiteralPath $where
  try {
    & $exe @argv 2>&1 | ForEach-Object {
      $line = Hide-Secret ([string]$_)
      $script:LastOutput += $line
      Write-Host ('  ' + $line)
      Add-LogLine ('  ' + $line)
    }
    $code = $LASTEXITCODE
  } finally {
    Pop-Location
  }
  Add-LogLine ('EXIT ' + $code)
  return $code
}

function Invoke-Py([string[]]$argv) {
  return (Invoke-Native 'python' $argv $ROOT)
}

function Invoke-Git([string[]]$argv) {
  # git, quietly: its answer as one trimmed string; the exit code in $script:GitCode
  $out = & git -C $ROOT @argv 2>$null
  $script:GitCode = $LASTEXITCODE
  if ($null -eq $out) { return '' }
  return ((@($out) | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Get-State {
  if (Test-Path -LiteralPath $STATE) {
    try { return (Get-Content -LiteralPath $STATE -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { Write-Host '  (the release state file could not be read)' -ForegroundColor DarkYellow }
  }
  return (New-Object PSObject)
}

function Set-State([string]$key, $value) {
  $s = Get-State
  $s | Add-Member -NotePropertyName $key -NotePropertyValue $value -Force
  $json = $s | ConvertTo-Json -Depth 4
  for ($i = 0; $i -lt 3; $i++) {
    try { Set-Content -LiteralPath $STATE -Value $json -Encoding UTF8 -ErrorAction Stop; Add-LogLine ('state: ' + $key + ' = ' + [string]$value); return } catch { Start-Sleep -Milliseconds 300 }
  }
  Say ('  Warning: ' + $key + ' could not be saved in ' + $STATE + ' (it is in the log: ' + [string]$value + ')') DarkYellow
}

function Get-Count([string]$pattern) {
  # the first number captured by $pattern in the last program's output; -1 when absent
  foreach ($l in $script:LastOutput) { if ($l -match $pattern) { return [int]$Matches[1] } }
  return -1
}

function Test-OutputHas([string]$pattern) {
  foreach ($l in $script:LastOutput) { if ($l -match $pattern) { return $true } }
  return $false
}

function Test-SameCommit([string]$a, [string]$b) {
  # two commit ids (any length from 7) name the same commit
  if (-not $a -or -not $b) { return $false }
  $n = [Math]::Min($a.Length, $b.Length)
  if ($n -lt 7) { return $false }
  return ($a.Substring(0, $n).ToLower() -eq $b.Substring(0, $n).ToLower())
}

function Get-LastLine([string[]]$lines) {
  # the last line that is not blank, trimmed and without surrounding quotes ('' when there is none): yq_backup
  # release ends with the path of its archive, yq_backup unpack with the folder it checked
  for ($i = $lines.Count - 1; $i -ge 0; $i--) {
    $t = ([string]$lines[$i]).Trim().Trim('"').Trim("'").Trim()
    if ($t) { return $t }
  }
  return ''
}

function Get-GhHeaders {
  # The headers of every call to api.github.com. While the repository is public GitHub is read without a key (60
  # calls an hour per PC is plenty for one release) and this returns no header at all. Once it is private, the
  # read-only token in GH_READ_TOKEN (fine-grained, this repository only: Actions read, Metadata read) goes out as
  # a Bearer header. It is taken from this PowerShell window's environment, else from the .env of this folder. The
  # value is never shown or logged (the masker is told it) and goes to api.github.com only, never to our own sites.
  if ($null -ne $script:GhHeaders) { return $script:GhHeaders }
  $tok = ([string]$env:GH_READ_TOKEN).Trim()
  if (-not $tok -and (Test-Path -LiteralPath $ENVFILE -PathType Leaf)) {
    foreach ($line in [System.IO.File]::ReadAllLines($ENVFILE)) {
      if ($line -match '^\s*GH_READ_TOKEN\s*=\s*(.*)$') { $tok = $Matches[1].Trim().Trim('"').Trim("'") }
    }
  }
  $h = @{}
  if ($tok) {
    $script:Secrets += $tok
    $h['Authorization'] = ('Bearer ' + $tok)
  }
  $script:GhHeaders = $h
  return $h
}

function Get-GhJson([string]$path) {
  # the GitHub API: no key while the repository is public, the GH_READ_TOKEN header once it is private
  try { return (Invoke-RestMethod -UseBasicParsing -Uri ('https://api.github.com/repos/' + $REPO + $path) -Headers (Get-GhHeaders) -UserAgent $UA -TimeoutSec 30) } catch { return $null }
}

function Get-Runs([string]$sha) {
  # the workflow runs (ci, cf-deploy) of one commit; $null when GitHub cannot be read
  $r = Get-GhJson ('/actions/runs?head_sha=' + $sha + '&per_page=30')
  if ($null -eq $r) { return $null }
  return @{ runs = @($r.workflow_runs) }
}

function Find-Run($wrap, [string]$name) {
  # newest first, as GitHub lists them
  foreach ($x in $wrap.runs) { if ([string]$x.name -eq $name) { return $x } }
  return $null
}

function Show-Run($run) {
  if ($null -eq $run) { return 'not started' }
  return ([string]$run.status + '/' + [string]$run.conclusion)
}

function Test-CfDeploySteps($run) {
  # cf-deploy can end "success" having deployed nothing (its secrets missing): both deploy steps must have run
  $j = $null
  try { $j = Invoke-RestMethod -UseBasicParsing -Uri ([string]$run.jobs_url) -Headers (Get-GhHeaders) -UserAgent $UA -TimeoutSec 30 } catch { return 'the jobs of the cf-deploy run could not be read' }
  $mStep = 'missing'
  $pStep = 'missing'
  foreach ($job in @($j.jobs)) {
    foreach ($s in @($job.steps)) {
      if ([string]$s.name -like 'Deploy the marketplace*') { $mStep = [string]$s.conclusion }
      if ([string]$s.name -like 'Deploy the portal*') { $pStep = [string]$s.conclusion }
    }
  }
  if ($mStep -ne 'success' -or $pStep -ne 'success') {
    return ('marketplace step ' + $mStep + ', portal step ' + $pStep + ' (skipped = the Cloudflare secrets are missing on GitHub, nothing was deployed)')
  }
  return ''
}

function Get-HealthCommit {
  try {
    $h = Invoke-RestMethod -UseBasicParsing -Uri ($API + '/health') -UserAgent $UA -TimeoutSec 70
    return [string]$h.commit
  } catch { return '' }
}

function Wait-Health([string]$want, [int]$minutes) {
  # polls the API's /health until it reports $want (the deployed commit); a cold start takes about a minute
  $deadline = (Get-Date).AddMinutes($minutes)
  while ($true) {
    $c = Get-HealthCommit
    if ($c) { Say ('  /health commit ' + $c) } else { Say '  /health: no answer yet' }
    if (Test-SameCommit $c $want) { return $true }
    if ((Get-Date) -gt $deadline) { return $false }
    Start-Sleep -Seconds 10
  }
}

function Get-MarketBuild {
  # yqmarketplace.com/version.json names the build that is live (the commit, 12 characters)
  try {
    $v = Invoke-RestMethod -UseBasicParsing -Uri ($MARKET + '/version.json?t=' + [DateTime]::UtcNow.Ticks) -UserAgent $UA -TimeoutSec 30
    if ($v -is [string]) { $v = $v | ConvertFrom-Json }
    return [string]$v.build
  } catch { return '' }
}

function Find-PortalBuild([string]$needle) {
  # The portal has no version.json: its build id (the commit, 12 characters) is compiled into one of its
  # script files. Read index.html, then follow the script files it and they name, until one carries it.
  $seen = @{}
  $queue = New-Object System.Collections.Queue
  $pat = '(?:assets/|\./)([A-Za-z0-9_.-]+\.js)'
  try { $html = [string](Invoke-WebRequest -UseBasicParsing -Uri ($PORTAL + '/?t=' + [DateTime]::UtcNow.Ticks) -UserAgent $UA -TimeoutSec 30).Content } catch { return '' }
  foreach ($m in [regex]::Matches($html, $pat)) { $queue.Enqueue($m.Groups[1].Value) }
  $n = 0
  while ($queue.Count -gt 0 -and $n -lt 250) {
    $f = [string]$queue.Dequeue()
    if ($seen.ContainsKey($f)) { continue }
    $seen[$f] = $true
    $n++
    try {
      $c = (Invoke-WebRequest -UseBasicParsing -Uri ($PORTAL + '/assets/' + $f) -UserAgent $UA -TimeoutSec 30).Content
      if ($c -is [byte[]]) { $c = [System.Text.Encoding]::UTF8.GetString($c) }
      $c = [string]$c
    } catch { continue }
    if ($c.Contains($needle)) { return $f }
    foreach ($m in [regex]::Matches($c, $pat)) {
      if (-not $seen.ContainsKey($m.Groups[1].Value)) { $queue.Enqueue($m.Groups[1].Value) }
    }
  }
  return ''
}

function Get-Newest([string]$dir, [string]$filter, [switch]$Directory) {
  if (-not (Test-Path -LiteralPath $dir)) { return $null }
  if ($Directory) {
    $items = Get-ChildItem -LiteralPath $dir -Filter $filter -Directory -ErrorAction SilentlyContinue
  } else {
    $items = Get-ChildItem -LiteralPath $dir -Filter $filter -File -ErrorAction SilentlyContinue
  }
  return ($items | Sort-Object LastWriteTime | Select-Object -Last 1)
}

function Use-CloudflareKeys {
  # wrangler reads CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID from the environment; .env names the token
  # CLOUDFLARE_API_TOKEN_WRITE (scripts\deploy_cf.py maps it the same way). Set for THIS PowerShell process
  # only; the values are never shown or logged, and the token is masked should any program print it.
  $found = @{}
  foreach ($line in [System.IO.File]::ReadAllLines($ENVFILE)) {
    if ($line -match '^\s*(CLOUDFLARE_ACCOUNT_ID|CLOUDFLARE_API_TOKEN_WRITE|CLOUDFLARE_API_TOKEN)\s*=\s*(.*)$') {
      $found[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
    }
  }
  $tok = $found['CLOUDFLARE_API_TOKEN_WRITE']
  if (-not $tok) { $tok = $found['CLOUDFLARE_API_TOKEN'] }
  if (-not $tok -or -not $found['CLOUDFLARE_ACCOUNT_ID']) { return $false }
  $script:Secrets += $tok
  $env:CLOUDFLARE_API_TOKEN = $tok
  $env:CLOUDFLARE_ACCOUNT_ID = $found['CLOUDFLARE_ACCOUNT_ID']
  $env:CI = 'true'
  $env:WRANGLER_SEND_METRICS = 'false'
  return $true
}
#endregion helpers

#region guards
function Assert-MainFolder {
  # Runs before anything else, before the log exists: a refusal writes nothing anywhere.
  $why = ''
  if ($ROOT -match '\\\.claude\\worktrees\\' -or $ROOT -match '/\.claude/worktrees/') {
    $why = 'this is a code worktree (.claude\worktrees\...)'
  } elseif (-not (Test-Path -LiteralPath (Join-Path $ROOT '.git') -PathType Container)) {
    $why = 'its .git is not a folder, so it is a worktree or a copy'
  } elseif (-not (Test-Path -LiteralPath (Join-Path $ROOT 'business_data') -PathType Container)) {
    $why = 'it has no business_data folder'
  } elseif (-not ((Test-Path -LiteralPath (Join-Path $ROOT 'Uplaods') -PathType Container) -or (Test-Path -LiteralPath (Join-Path $ROOT 'Uploads') -PathType Container))) {
    $why = 'it has no drop folder (Uplaods or Uploads)'
  }
  if ($why) {
    Write-Host ('REFUSED: ' + $ROOT + ' is not the main project folder: ' + $why + '.') -ForegroundColor Red
    Write-Host 'Run this script from the main project folder, on branch main, after git pull --ff-only. A worktree finds the main folder .env, which is production.' -ForegroundColor Red
    exit 2
  }
}

function Assert-Ready([switch]$NewTrain, [switch]$Lenient) {
  # Every part: branch main, at origin/main, no changed tracked file, no Focus load running, .env present,
  # and the same commit as -Part backup (a new backup starts a new train; the rollback only notes it).
  Step 'guards: branch main, at origin/main, no changed files, no load running, .env present'
  $branch = Invoke-Git @('rev-parse', '--abbrev-ref', 'HEAD')
  if ($branch -ne 'main') { Stop-Part 2 ('This folder is on branch ' + $branch + ', not main. Tell Claude.') }
  $script:HEAD = Invoke-Git @('rev-parse', 'HEAD')
  if ($script:HEAD.Length -ne 40) { Fail 'git rev-parse HEAD' }
  [void](Invoke-Git @('fetch', '-q', 'origin', 'main'))
  if ($script:GitCode -ne 0) { Fail 'git fetch origin main (is the internet on?)' }
  $origin = Invoke-Git @('rev-parse', 'origin/main')
  if ($origin.Length -ne 40) { Fail 'git rev-parse origin/main' }
  if ($script:HEAD -ne $origin) {
    if ($Lenient) {
      Say ('  Note: this folder is at ' + $script:HEAD.Substring(0, 12) + ', origin/main at ' + $origin.Substring(0, 12) + '; the rollback goes on with this folder as it is.') Yellow
    } else {
      Stop-Part 2 'This folder is not at origin/main: run  git pull --ff-only  and then this part again (if git refuses, tell Claude).'
    }
  }
  $dirty = Invoke-Git @('status', '--porcelain', '--untracked-files=no')
  if ($dirty) {
    Say $dirty
    if ($Lenient) {
      Say '  Note: tracked files are changed in this folder (listed above); the rollback goes on.' Yellow
    } else {
      Stop-Part 2 'Tracked files are changed in this folder (listed above). Do not discard anything: tell Claude.'
    }
  }
  if (Test-Path -LiteralPath $LOCK) {
    Stop-Part 2 'business_data\ops\LOCK exists: a Focus load is running or was cut off. In Claude Code, in this folder, type /yq-load to settle it, then run this part again. Never delete the lock by hand.'
  }
  if (-not (Test-Path -LiteralPath $ENVFILE -PathType Leaf)) { Stop-Part 2 'There is no .env in this folder, so the tools cannot reach the database. Tell Claude.' }
  $was = [string](Get-State).release_sha
  if ($was -and $was -ne $script:HEAD) {
    if ($NewTrain) {
      Say ('  Note: the last release state is for ' + $was.Substring(0, 12) + '; this run is for ' + $script:HEAD.Substring(0, 12) + '.') Yellow
    } elseif ($Lenient) {
      Say ('  Note: the release was made from ' + $was.Substring(0, 12) + '; this folder is at ' + $script:HEAD.Substring(0, 12) + '.') Yellow
    } else {
      Stop-Part 2 ('This folder moved since -Part backup (' + $was.Substring(0, 12) + ' -> ' + $script:HEAD.Substring(0, 12) + '). Run -Part check and -Part backup again for the new commit, or tell Claude.')
    }
  }
  Say ('  ok: main at ' + $script:HEAD.Substring(0, 12) + ', no load running, .env present')
}
#endregion guards

#region part:check
function Invoke-PartCheck {
  # Reads only. About a minute.
  Assert-Ready -NewTrain
  Say ('  release commit: ' + (Invoke-Git @('log', '-1', '--format=%h %s', 'HEAD')))

  Step 'the release files are here'
  foreach ($f in @($R8M, $R8M_REV, $R8N, $R8N_REV, 'scripts\market_intel_import.py', 'scripts\market_intel_thumbs.py',
                   'scripts\apply_sql.py', 'scripts\audit_grants.py', 'scripts\db_backup.py', 'scripts\yq_backup.py',
                   'scripts\prod_gate.py', 'scripts\render_deploy.py')) {
    [void](Invoke-Git @('ls-files', '--error-unmatch', ($f -replace '\\', '/')))
    if ($script:GitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $ROOT $f) -PathType Leaf)) {
      Stop-Part 2 ($f + ' is not in this folder: is the release merged into main and pulled? Tell Claude.')
    }
    Say ('  ok ' + $f)
  }

  Step '.env holds the keys the tools need (names only, never the values)'
  foreach ($k in @('DATABASE_URL', 'RENDER_API_KEY', 'SUPABASE_URL', 'SUPABASE_KEY')) {
    if (Select-String -LiteralPath $ENVFILE -Pattern ('^\s*' + $k + '\s*=\s*\S') -Quiet) {
      Say ('  ok ' + $k)
    } else {
      Stop-Part 2 ('.env has no ' + $k + '. Tell Claude (the name only, never a value).')
    }
  }
  foreach ($k in @('CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_API_TOKEN_WRITE')) {
    if (Select-String -LiteralPath $ENVFILE -Pattern ('^\s*' + $k + '\s*=\s*\S') -Quiet) {
      Say ('  ok ' + $k + ' (only a web rollback uses it)')
    } else {
      Say ('  .env has no ' + $k + ': only a web rollback needs it, and that can also be done in the Cloudflare dashboard') Yellow
    }
  }

  Step 'python'
  if ((Invoke-Py @('--version')) -ne 0) { Fail 'python --version' }
  Say '  -Part backup needs the backup password and 7-Zip, as the pre-load backups do; yq_backup says so if one is missing.'

  Step 'GitHub: ci must be green for this commit'
  $w = Get-Runs $script:HEAD
  if ($null -eq $w) {
    Say ('  GitHub could not be read (no internet, or the repository is private and GH_READ_TOKEN is missing or has expired). Open https://github.com/' + $REPO + '/actions and look at the "ci" run of commit ' + $script:HEAD.Substring(0, 7) + '.') Yellow
    $a = Read-Host 'Type YES if ci shows a green tick for that commit'
    if (([string]$a).Trim() -ne 'YES') { Stop-Part 2 'Stopped: ci was not confirmed.' }
    Add-LogLine 'ci confirmed by hand'
  } else {
    $ci = Find-Run $w 'ci'
    $cf = Find-Run $w 'cf-deploy'
    Say ('  ci: ' + (Show-Run $ci) + '   cf-deploy: ' + (Show-Run $cf))
    if ($null -eq $ci) { Stop-Part 2 'GitHub shows no ci run for this commit yet: wait a minute, then run -Part check again.' }
    if ([string]$ci.status -ne 'completed') { Stop-Part 2 'ci is still running (5-10 minutes after the push): wait until it is green, then run -Part check again.' }
    if ([string]$ci.conclusion -ne 'success') { Stop-Part 1 ('ci FAILED on this commit (' + [string]$ci.html_url + '). Do not release: tell Claude.') }
    Say '  ci is green. cf-deploy puts the new portal and marketplace live by itself after it; -Part web checks that.'
  }

  Step 'what is live now (reads only)'
  if ((Invoke-Py @('-m', 'scripts.render_deploy', 'status')) -ne 0) { Fail 'render_deploy status (is RENDER_API_KEY right?)' }
  Say ('  API /health commit:  ' + (Get-HealthCommit))
  Say ('  marketplace build:   ' + (Get-MarketBuild))
  Say ('  Bahrain time now:    ' + (Get-Date -Format 'ddd dd-MMM HH:mm') + '  (best at a quiet hour, never during a Focus load)')
  Say 'Check passed. Next: -Part backup' Green
}
#endregion part:check

#region part:backup
function Invoke-PartBackup {
  # Reads production only. About 5-10 minutes, one YES.
  Assert-Ready -NewTrain
  $st = Get-State
  # The FIRST backup and before-snapshot of this release stay the rollback point: a later run (the same commit
  # again, or a fix pushed after a partial release) is recorded as the latest and never replaces them.
  $kept = [string]$st.backup_archive
  $hasFirst = $kept -and (Test-Path -LiteralPath $kept -PathType Leaf)
  if ($hasFirst) { Say ('  The first backup of this release (' + $kept + ') stays the rollback point; this one is recorded as the latest.') Yellow }

  # Each attempt takes a label of its own (mi-v2, then mi-v2-2, mi-v2-3 ...): yq_backup refuses a second backup of one
  # label on one day, and a run that stopped half-way leaves its folder behind. The attempt is counted once it starts.
  $runs = 1
  if ([string]$st.backup_runs -match '^\d+$') { $runs = [int]$st.backup_runs + 1 }
  $label = 'mi-v2'
  if ($runs -gt 1) { $label = 'mi-v2-' + $runs }

  Step ('backup of every table, encrypted outside OneDrive (yq_backup release ' + $label + '), + the before-snapshot (gate)')
  Confirm-Step ('Back up every table of the live database (python -m scripts.yq_backup release ' + $label + ': checked, encrypted, read back, the plain copy deleted; outside OneDrive) and take the gate snapshot pre-mi-v2. It only READS production (about 5-10 minutes).')
  Set-State 'backup_runs' $runs
  $code = (Invoke-Py @('-m', 'scripts.yq_backup', 'release', $label))
  if ($code -eq 2) { Stop-Part 2 'yq_backup refused (its reason is above): nothing was made. If it says this backup is already there, run -Part backup again (the next run takes a new label); otherwise tell Claude.' }
  if ($code -ne 0) { Fail ('yq_backup release ' + $label + ' did not finish (its last line says what is left plain: ' + (Get-LastLine $script:LastOutput) + ')') }
  $arc = Get-LastLine $script:LastOutput
  if (-not $arc -or -not (Test-Path -LiteralPath $arc -PathType Leaf)) { Fail ('yq_backup did not end with the path of its archive (its last line: ' + $arc + ')') }
  if ((Invoke-Py @('-m', 'scripts.prod_gate', 'snapshot', '--label', 'pre-mi-v2')) -ne 0) { Fail 'gate snapshot pre-mi-v2 (it exits 1 when foreign-key orphans exist)' }
  $g = Get-Newest $GATES '*_pre-mi-v2.json'
  Set-State 'release_sha' $script:HEAD
  Set-State 'backup_latest' $arc
  if (-not $hasFirst) {
    Set-State 'backup_archive' $arc
    if ($g) { Set-State 'gate_pre' ('business_data\gates\' + $g.Name) }
  }
  Say ('Backup done: ' + $arc + '. Next: -Part migrate') Green
}
#endregion part:backup

#region part:migrate
function Invoke-Migration([string]$label, [string]$file) {
  # rehearse (runs on production, always rolled back) -> apply (one transaction) -> grant audit
  Step ('migration ' + $label + ': ' + $file)
  Confirm-Step ('REHEARSE ' + $label + ' on production: every statement runs, then it is ROLLED BACK; nothing is kept.')
  if ((Invoke-Py @('-m', 'scripts.apply_sql', $file, '--rehearse')) -ne 0) { Fail ('the rehearsal of ' + $label + ' (nothing was changed)') }
  Confirm-Step ('APPLY ' + $label + ' to production: additive, one transaction; it waits at most 2 s for a lock, else it fails with nothing applied.')
  if ((Invoke-Py @('-m', 'scripts.apply_sql', $file)) -ne 0) { Fail ('the apply of ' + $label + ' (one transaction: nothing of this file was kept); at a quieter moment run -Part migrate again') }
  if ((Invoke-Py @('-m', 'scripts.audit_grants')) -ne 0) { Fail ('audit_grants after ' + $label + ': something is open to the public key') }
  Set-State ('migrated_' + $label) (Get-Date -Format 's')
}

function Invoke-PartMigrate {
  # About 3 minutes, four YES.
  Assert-Ready
  $arc = [string](Get-State).backup_archive
  if (-not $arc -or -not (Test-Path -LiteralPath $arc -PathType Leaf)) { Stop-Part 2 'There is no backup of this release yet: run -Part backup first.' }
  Say ('  backup of this release: ' + $arc)
  Invoke-Migration 'r8m' $R8M
  Invoke-Migration 'r8n' $R8N
  Say 'Both migrations are applied and the grant audit is clean. Next: -Part api' Green
}
#endregion part:migrate

#region part:api
function Invoke-PartApi {
  # 5-10 minutes, one YES.
  Assert-Ready
  $st = Get-State
  if (-not $st.migrated_r8m -or -not $st.migrated_r8n) {
    Say '  Note: the state does not show both migrations applied. The new API copes (its new pages say Not available yet until they are), but the order is -Part migrate first.' Yellow
  }

  Step 'the API that is live now: the rollback target'
  if ((Invoke-Py @('-m', 'scripts.render_deploy', 'status')) -ne 0) { Fail 'render_deploy status' }
  $prev = ''
  $prevCommit = ''
  foreach ($l in $script:LastOutput) {
    if ($l -match '^\s*(dep-[A-Za-z0-9]+)\s+live\b\s*([0-9a-f]{7,40})?') { $prev = $Matches[1]; $prevCommit = [string]$Matches[2]; break }
  }
  if (-not $prev) { Fail 'render_deploy status shows no live deploy' }

  if (Test-SameCommit $prevCommit $script:HEAD) {
    Say ('  The API already runs ' + $prevCommit + ', the release: nothing to deploy.') Green
  } else {
    if ([string]$st.api_previous_deploy) {
      # a deploy of this release was tried before: the deploy live BEFORE the release stays the rollback target
      Say ('  Rollback target kept from the first try: ' + [string]$st.api_previous_deploy + ' (commit ' + [string]$st.api_previous_commit + '); live now: ' + $prev) Yellow
    } else {
      Set-State 'api_previous_deploy' $prev
      Set-State 'api_previous_commit' $prevCommit
      Say ('  Rollback target written down: ' + $prev + ' (commit ' + $prevCommit + ')') Yellow
    }
    Step 'deploy the API'
    Confirm-Step ('Deploy the API at commit ' + $script:HEAD.Substring(0, 12) + ' to Render (5-10 minutes). The old API keeps answering until the new one is live.')
    if ((Invoke-Py @('-m', 'scripts.render_deploy', 'deploy', '--commit', $script:HEAD, '--wait')) -ne 0) { Fail 'the Render deploy (the old API is still live and works)' }
  }

  Step '/health must report the release commit'
  if (-not (Wait-Health $script:HEAD 8)) { Fail ('/health does not report ' + $script:HEAD.Substring(0, 12) + ' after 8 minutes (look at the Render dashboard)') }
  Set-State 'api_live' (Get-Date -Format 's')
  Say 'The API runs the release. Next: -Part web' Green
}
#endregion part:api

#region part:web
function Invoke-PartWeb {
  # Reads only: the push to main already deployed the web (cf-deploy, after ci). 1-15 minutes.
  Assert-Ready
  $sha = $script:HEAD

  Step 'the cf-deploy run of this commit'
  $w = Get-Runs $sha
  if ($null -eq $w) {
    Say ('  GitHub could not be read. Open https://github.com/' + $REPO + '/actions: the "cf-deploy" run of commit ' + $sha.Substring(0, 7) + ' must be green.') Yellow
    $a = Read-Host 'Type YES if it is green'
    if (([string]$a).Trim() -ne 'YES') { Stop-Part 2 'Stopped: cf-deploy was not confirmed.' }
    Add-LogLine 'cf-deploy confirmed by hand'
  } else {
    $cf = Find-Run $w 'cf-deploy'
    if ($null -eq $cf) {
      # this commit changed no web file: the web live is the last cf-deploy build, if web\ is unchanged since
      $wf = Get-GhJson '/actions/workflows/cf-deploy.yml/runs?branch=main&per_page=1'
      if ($null -eq $wf -or @($wf.workflow_runs).Count -eq 0) { Fail 'no cf-deploy run found on GitHub' }
      $cf = @($wf.workflow_runs)[0]
      [void](Invoke-Git @('diff', '--quiet', [string]$cf.head_sha, 'HEAD', '--', 'web', '.github/workflows/cf-deploy.yml'))
      if ($script:GitCode -ne 0) {
        Stop-Part 1 'There is no cf-deploy run for this commit, and web\ changed since the last one. On GitHub: Actions -> cf-deploy -> Run workflow (branch main); when it is green, run -Part web again.'
      }
      $sha = [string]$cf.head_sha
      Say ('  This commit changed no web file; the web is the build of ' + $sha.Substring(0, 12) + '.')
    }
    $deadline = (Get-Date).AddMinutes(45)
    while ([string]$cf.status -ne 'completed') {
      if ((Get-Date) -gt $deadline) { Fail 'cf-deploy did not finish in 45 minutes' }
      Say ('  cf-deploy: ' + (Show-Run $cf) + ' (it waits for ci); looking again in 60 s')
      Start-Sleep -Seconds 60
      $w2 = Get-Runs $sha
      if ($w2) {
        $again = Find-Run $w2 'cf-deploy'
        if ($again) { $cf = $again }
      }
    }
    Say ('  cf-deploy: ' + (Show-Run $cf) + '  ' + [string]$cf.html_url)
    if ([string]$cf.conclusion -ne 'success') {
      Stop-Part 1 'cf-deploy did not succeed: the previous web is still live (the new API answers its pages as before). When ci is green, on GitHub: Actions -> cf-deploy -> Run workflow (branch main), then -Part web again.'
    }
    $bad = Test-CfDeploySteps $cf
    if ($bad) { Stop-Part 1 ('cf-deploy ended without deploying both sites: ' + $bad + '. Tell Claude.') }
  }

  $want = $sha.Substring(0, 12)
  Step ('the live marketplace shows build ' + $want)
  $mb = ''
  for ($i = 0; $i -lt 6; $i++) {
    $mb = Get-MarketBuild
    if ($mb -eq $want) { break }
    Start-Sleep -Seconds 30
  }
  Say ('  yqmarketplace.com/version.json build: ' + $mb)
  if ($mb -ne $want) { Fail ('the marketplace shows build ' + $mb + ', expected ' + $want) }

  Step ('the live portal carries build ' + $want)
  Say '  reading the portal script files for the build id (a minute or two)'
  $hit = Find-PortalBuild $want
  if ($hit) {
    Say ('  ops.yqmarketplace.com carries build ' + $want + ' (in ' + $hit + ')')
  } else {
    Say ('  The build id ' + $want + ' was not found in the portal files.') Yellow
    Say '  Open https://ops.yqmarketplace.com/market-intel in a private window: the tabs Cases and Supplier reports must be there.' Yellow
    $a = Read-Host 'Type YES if they are there'
    if (([string]$a).Trim() -ne 'YES') { Fail 'the portal does not show the new Market Intel' }
    Add-LogLine 'portal confirmed by hand'
  }
  Set-State 'web_live' (Get-Date -Format 's')
  Say 'The new portal and marketplace are live. Next: -Part import' Green
}
#endregion part:web

#region part:import
function Invoke-PartImport {
  # 15-25 minutes, up to four YES. Run it again after a stop: it resumes and never duplicates.
  Assert-Ready

  Step 'the old Product Finds and Field Notes: dry run (reads only; it opens every photo, a few minutes)'
  if ((Invoke-Py @('-m', 'scripts.market_intel_import')) -ne 0) { Fail 'the import dry run' }
  if (-not (Test-OutputHas '^schema: the R7b tables with the r8m photo columns')) { Stop-Part 2 'The r8m columns are not in the database yet: run -Part migrate first.' }
  $todo = Get-Count '^to create: (\d+) sighting'
  if ($todo -lt 0) { Fail 'the dry run summary could not be read' }
  $resume = Test-OutputHas '^to complete \('
  if ($todo -eq 0 -and -not $resume) {
    Say '  Nothing left to import: the old finds are already in Market Intel.' Green
  } else {
    Step 'trial: the 5 oldest'
    Confirm-Step ('Copy the 5 oldest of the ' + $todo + ' sightings into Market Intel. A COPY: product_finds and field_notes are never changed.')
    if ((Invoke-Py @('-m', 'scripts.market_intel_import', '--apply', '--limit', '5')) -ne 0) { Fail 'the import trial (run -Part import again: it resumes, never duplicates)' }
    Step 'everything left'
    Confirm-Step 'Copy everything left, each photo with its thumbnail (5-15 minutes).'
    if ((Invoke-Py @('-m', 'scripts.market_intel_import', '--apply')) -ne 0) { Fail 'the import (run -Part import again: it resumes, never duplicates)' }
    Step 'once more: it must create nothing'
    Confirm-Step 'Run the import a second time. It must create 0, which proves a re-run is harmless.'
    if ((Invoke-Py @('-m', 'scripts.market_intel_import', '--apply')) -ne 0) { Fail 'the second import run' }
    if (-not (Test-OutputHas '^created: 0 sightings') -or (Test-OutputHas '^completed \(')) {
      Fail 'the second run still wrote something (it must create 0); do not go on to the thumbnails'
    }
    Say '  The second run created 0: the import is complete and safe to repeat.' Green
  }

  Step 'thumbnails and blurred copies for the older photos: dry run (reads only)'
  if ((Invoke-Py @('-m', 'scripts.market_intel_thumbs')) -ne 0) { Fail 'the thumbnail dry run' }
  $thumbs = Get-Count '^Photos without a thumbnail: (\d+)'
  $blurs = Get-Count 'flagged photos without a blurred copy: (\d+)'
  if ($thumbs -lt 0 -or $blurs -lt 0) { Fail 'the thumbnail dry run could not be read' }
  if ($thumbs -eq 0 -and $blurs -eq 0) {
    Say '  Every photo has its thumbnail.' Green
  } else {
    Confirm-Step ('Make ' + $thumbs + ' thumbnail(s) and ' + $blurs + ' blurred copy(ies) next to the photos; the photos themselves are only read.')
    if ((Invoke-Py @('-m', 'scripts.market_intel_thumbs', '--apply', '--limit', '1000')) -ne 0) { Fail 'some thumbnails could not be made (listed above; the lists show the full photo for those)' }
    if ((Invoke-Py @('-m', 'scripts.market_intel_thumbs')) -ne 0) { Fail 'the thumbnail recount' }
  }
  Set-State 'import_done' (Get-Date -Format 's')
  Say 'Import done. Next: -Part verify' Green
}
#endregion part:import

#region part:verify
function Invoke-PartVerify {
  # Reads only. About 2 minutes, then your checks in the portal.
  Assert-Ready
  $st = Get-State

  Step 'gate snapshot after the release, compared with the one before'
  if ((Invoke-Py @('-m', 'scripts.prod_gate', 'snapshot', '--label', 'post-mi-v2')) -ne 0) { Fail 'gate snapshot post-mi-v2' }
  $pre = [string]$st.gate_pre
  if (-not $pre -or -not (Test-Path -LiteralPath (Join-Path $ROOT $pre))) {
    $g = Get-Newest $GATES '*_pre-mi-v2.json'
    if ($g) { $pre = 'business_data\gates\' + $g.Name } else { $pre = '' }
  }
  if (-not $pre) { Fail 'there is no pre-mi-v2 gate snapshot (run -Part backup first)' }
  $post = Get-Newest $GATES '*_post-mi-v2.json'
  if (-not $post) { Fail 'the post-mi-v2 snapshot file was not found' }
  if ((Invoke-Py @('-m', 'scripts.prod_gate', 'compare', $pre, ('business_data\gates\' + $post.Name))) -ne 0) {
    Fail 'gate compare: a pre-existing order, line or total changed without an explanation. Stop rule: tell Claude now (docs\RELEASE.md, Rollback)'
  }

  Step 'the API and the web'
  $hc = Get-HealthCommit
  Say ('  API /health commit: ' + $hc)
  if (-not (Test-SameCommit $hc $script:HEAD)) { Fail 'the API does not run the release commit: run -Part api' }
  Say ('  marketplace build:  ' + (Get-MarketBuild))

  Step 'your checks in the portal (about 10 minutes; nothing here is automatic)'
  $checks = @(
    '  1. Lists (Market Intel -> Lists, as admin): add your first real supplier, one of its brands and at least',
    '     one quality issue type (English and Arabic label; safety on for heat, smoke, swelling or a shock).',
    '  2. Spotted with a photo: the camera button on the Market Intel page, or Spotted in the rep app. Take a',
    '     photo, choose Quality problem, the product and the issue, Save. The card says it was saved.',
    '  3. A case: Market Intel -> Cases -> New shows that report as a case, with its thumbnail and timeline.',
    '     A case can never be deleted (its timeline is permanent). If it was only a test: Not confirmed, with',
    '     the reason "release test", then Closed.',
    '  4. Supplier reports: build a draft for that supplier and this month. It opens as a Draft; leave it so.',
    '  5. Library: the old finds are there, with small thumbnails.'
  )
  foreach ($l in $checks) { Say $l }
  Set-State 'verified' (Get-Date -Format 's')
  Say 'Verified by the script. Do the five checks above, then the after-release list in docs\MARKET_INTEL_RELEASE.md.' Green
}
#endregion part:verify

#region part:rollback
function Undo-Migration([string]$label, [string]$file, [string]$whenRefused) {
  # rehearse the reverse (rolled back) -> apply -> grant audit; a refusal changes nothing and is explained
  Step ('database: the ' + $label + ' reverse (' + $file + ')')
  if (-not (Ask-Yes ('REHEARSE the ' + $label + ' reverse on production (rolled back, nothing is kept). Anything else leaves ' + $label + ' as it is.'))) {
    Say ('  ' + $label + ' stays.') Yellow
    return $false
  }
  if ((Invoke-Py @('-m', 'scripts.apply_sql', $file, '--rehearse')) -ne 0) {
    Say ('  The ' + $label + ' reverse refused or failed in the rehearsal (above); nothing was changed. ' + $whenRefused) Yellow
    return $false
  }
  if (-not (Ask-Yes ('APPLY the ' + $label + ' reverse to production.'))) {
    Say ('  ' + $label + ' stays.') Yellow
    return $false
  }
  if ((Invoke-Py @('-m', 'scripts.apply_sql', $file)) -ne 0) { Fail ('the ' + $label + ' reverse (one transaction: nothing of it was kept)') }
  if ((Invoke-Py @('-m', 'scripts.audit_grants')) -ne 0) { Fail ('audit_grants after the ' + $label + ' reverse') }
  return $true
}

function Invoke-PartRollback {
  # Only when something is wrong after the release. Order: web -> API -> database (only when Claude says so)
  # -> a table restore (last resort, with Claude). Each piece asks YES; anything else skips that piece.
  Assert-Ready -Lenient
  $st = Get-State
  Say 'Rollback order: the web, then the API, then (only when Claude says so) the database. Each piece asks for YES; anything else skips it.' Yellow

  Step 'R1. web (Cloudflare): the portal back to the version before the release'
  if (-not (Use-CloudflareKeys)) {
    Say '  .env has no CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN_WRITE: roll the web back in the Cloudflare dashboard (Workers -> the Worker -> Deployments), or tell Claude.' Yellow
  } else {
    Say '  Cloudflare keys loaded from .env for this window only (not shown).'
    foreach ($site in @(@{ name = 'portal'; cfg = 'wrangler.portal.jsonc'; note = 'Market Intel v2 lives in the portal.' },
                        @{ name = 'marketplace'; cfg = 'wrangler.market.jsonc'; note = 'The marketplace code did not change in this release (only rebuilt): leave it unless it misbehaves.' })) {
      Say ('  ' + $site.name + ': ' + $site.note)
      if ((Invoke-Native 'npx' @('--yes', 'wrangler@4', 'versions', 'list', '-c', $site.cfg) (Join-Path $ROOT 'web')) -ne 0) { Fail ('wrangler versions list (' + $site.name + ')') }
      $id = ([string](Read-Host ('Version id to put back for the ' + $site.name + ' (the newest one created BEFORE the release), or Enter to leave it'))).Trim()
      Add-LogLine ('version id typed for the ' + $site.name + ': ' + $id)
      if (-not $id) {
        Say ('  ' + $site.name + ': left as it is.')
        continue
      }
      if ($id -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') { Fail ('not a version id: ' + $id) }
      if (Ask-Yes ('Switch the ' + $site.name + ' to version ' + $id + ' (all of its traffic, at once).')) {
        if ((Invoke-Native 'npx' @('--yes', 'wrangler@4', 'versions', 'deploy', ($id + '@100%'), '-c', $site.cfg, '-y') (Join-Path $ROOT 'web')) -ne 0) { Fail ('wrangler versions deploy (' + $site.name + ')') }
      } else {
        Say ('  ' + $site.name + ': left as it is.') Yellow
      }
    }
  }

  Step 'R2. API (Render): back to the deploy that was live before the release'
  if ((Invoke-Py @('-m', 'scripts.render_deploy', 'status')) -ne 0) { Fail 'render_deploy status' }
  $dep = [string]$st.api_previous_deploy
  if ($dep) { Say ('  -Part api wrote down: ' + $dep + ' (commit ' + [string]$st.api_previous_commit + ')') }
  $typed = ([string](Read-Host ('Deploy id to roll back to (Enter = ' + $dep + ')'))).Trim()
  if ($typed) { $dep = $typed }
  Add-LogLine ('deploy id for the API rollback: ' + $dep)
  if (-not $dep) {
    Say '  No deploy id: the API is left as it is.' Yellow
  } elseif ($dep -notmatch '^dep-[A-Za-z0-9]+$') {
    Fail ('not a Render deploy id: ' + $dep)
  } elseif (Ask-Yes ('Roll the API back to ' + $dep + ' (Render switches back in a few minutes).')) {
    if ((Invoke-Py @('-m', 'scripts.render_deploy', 'rollback', '--deploy', $dep, '--wait')) -ne 0) { Fail 'the Render rollback' }
    if ($dep -eq [string]$st.api_previous_deploy -and [string]$st.api_previous_commit) {
      if (-not (Wait-Health ([string]$st.api_previous_commit) 8)) { Fail '/health does not report the earlier commit after 8 minutes' }
    } else {
      Say ('  API /health commit now: ' + (Get-HealthCommit))
    }
  } else {
    Say '  The API is left as it is.' Yellow
  }

  Step 'R3. database: only when Claude says so (the new tables and columns are additive; the old API and pages ignore them)'
  $okN = Undo-Migration 'r8n' $R8N_REV 'It refuses only when product_finds or field_notes is gone, or when a lock did not come within 2 s: wait a minute and try again.'
  $okM = Undo-Migration 'r8m' $R8M_REV 'r8m refuses while it holds data (the lists, cases, reports, the capture times of the imported finds, the photo thumbnails): that is its protection. Leave it; the old API and pages ignore it. Dropping it anyway is a decision for Claude (docs\MIGRATIONS.md, Release R8m).'
  Add-LogLine ('reverses: r8n ' + $okN + ', r8m ' + $okM)

  Step 'R4. last resort, only with Claude: restore tables from the encrypted backup taken before the release'
  $arc = [string]$st.backup_archive
  if (-not $arc -or -not (Test-Path -LiteralPath $arc -PathType Leaf)) {
    Say '  The state names no encrypted backup of this release, or the file is gone: nothing to restore from here. An older plain backup is restored with Claude (docs\BACKUPS.md, Restoring).' Yellow
  } else {
    Say ('  backup: ' + $arc)
    Say '  The restore refuses by itself when live rows are newer than the backup (orders or sightings since the release): it never deletes them.'
    $tables = ([string](Read-Host 'Tables to restore, comma-separated, exactly as Claude gave them (or Enter to skip)')).Trim()
    Add-LogLine ('tables typed for the restore: ' + $tables)
    if (-not $tables) {
      Say '  No restore.'
    } elseif ($tables -cnotmatch '^[a-z_][a-z0-9_]*(,[a-z_][a-z0-9_]*)*$') {
      Fail ('not a table list: ' + $tables)
    } else {
      # a new folder next to the archive, in the backup store: yq_backup refuses one in OneDrive or the project folder
      $scratch = Join-Path (Split-Path -Parent $arc) ([IO.Path]::GetFileNameWithoutExtension($arc) + '_unpacked_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
      Say ('  unpacked to: ' + $scratch + ' (outside OneDrive; it holds the backup unencrypted: Claude deletes it once the restore is done)')
      if ((Invoke-Py @('-m', 'scripts.yq_backup', 'unpack', $arc, '--to', $scratch)) -ne 0) { Fail 'yq_backup unpack (its last line says what is left): nothing was restored' }
      # unpack prints the checked folder (the one that holds manifest.json) as its last line
      $dir = Get-LastLine $script:LastOutput
      if (-not $dir -or -not (Test-Path -LiteralPath (Join-Path $dir 'manifest.json') -PathType Leaf)) { Fail ('yq_backup unpack did not end with the folder of the backup (its last line: ' + $dir + ')') }
      if ((Invoke-Py @('-m', 'scripts.db_backup', '--verify', $dir)) -ne 0) { Fail 'the unpacked backup does not match its manifest' }
      if ((Invoke-Py @('-m', 'scripts.db_backup', '--restore', $dir, '--tables', $tables)) -ne 0) { Fail 'the restore dry run refused (above); nothing was changed' }
      if (Ask-Yes ('RESTORE ' + $tables + ' from the backup taken before the release: one transaction, those tables get the rows of the backup.')) {
        if ((Invoke-Py @('-m', 'scripts.db_backup', '--restore', $dir, '--tables', $tables, '--yes')) -ne 0) { Fail 'the restore (rolled back as a whole on any mismatch)' }
        Set-State 'restored_tables' $tables
      } else {
        Say '  No restore.' Yellow
      }
    }
  }

  Step 'R5. gate snapshot after the rollback, compared with the one before the release'
  if ((Invoke-Py @('-m', 'scripts.prod_gate', 'snapshot', '--label', 'post-mi-v2-rollback')) -ne 0) { Fail 'gate snapshot post-mi-v2-rollback' }
  $pre = [string]$st.gate_pre
  $post = Get-Newest $GATES '*_post-mi-v2-rollback.json'
  if ($pre -and $post -and (Test-Path -LiteralPath (Join-Path $ROOT $pre))) {
    if ((Invoke-Py @('-m', 'scripts.prod_gate', 'compare', $pre, ('business_data\gates\' + $post.Name))) -ne 0) { Fail 'gate compare after the rollback: tell Claude now' }
  } else {
    Say '  No pre-mi-v2 snapshot to compare with: tell Claude.' Yellow
  }
  Set-State 'rolled_back' (Get-Date -Format 's')
  Say 'Rollback finished for the pieces you said YES to. Tell Claude what happened.' Green
}
#endregion part:rollback

# ---------------------------------------------------------------------------------------------------- run
Assert-MainFolder
Set-Location -LiteralPath $ROOT
if (-not (Test-Path -LiteralPath $RELDIR)) { [void](New-Item -ItemType Directory -Path $RELDIR) }
Add-LogLine ('==== part ' + $Part + ' started in ' + $ROOT)
try {
  switch ($Part) {
    'check'    { Invoke-PartCheck }
    'backup'   { Invoke-PartBackup }
    'migrate'  { Invoke-PartMigrate }
    'api'      { Invoke-PartApi }
    'web'      { Invoke-PartWeb }
    'import'   { Invoke-PartImport }
    'verify'   { Invoke-PartVerify }
    'rollback' { Invoke-PartRollback }
  }
} catch {
  Say ('UNEXPECTED ERROR: ' + $_.Exception.Message) Red
  Stop-Part 1 ('Stopped on an unexpected error (above). Tell Claude (log: ' + $LOG + ').')
}
Stop-Part 0 ('Part ' + $Part + ' finished. Log: ' + $LOG)
