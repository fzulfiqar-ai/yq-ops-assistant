# Phase 2b (release r9) - the release script. Prepared by Claude, 21-Oct-2026; the owner runs it.
#
# What goes live: the portal's Stock Movement page pairs the transfers by their contents (the same code as /yq-moves,
# scripts\ops\pairing.py, run by the API over the new view v_transfer_lines) and takes each van's stock from the
# ledger; ID numbers in the stored stock narrations are masked; the four load fixes; the team message with its Copy
# button in the Command Centre (the table team_messages). The runbook in plain words (each part, its time, the
# rollback, the after-release list) is docs\RELEASE.md, section "Release 2b (r9)". Read it once before the first part.
#
# Run it from the MAIN project folder, on branch main, after  git pull --ff-only  . It refuses to run from a
# code worktree (.claude\worktrees\...): a worktree finds the main folder's .env, which is production.
#
#   powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part check
#   powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part backup
#   powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part migrate
#   powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part api
#   powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part web
#   powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part verify
#   powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part migrate2
# and only if something is wrong after the release:
#   powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part rollback
#   powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part rollback -RestoreTables <t1,t2>
#
# Order: check -> backup -> migrate -> api -> web -> verify -> migrate2. One part at a time; each part can be run
# again on its own. The web goes live AFTER the API: the release commit carries no web change; once -Part api has
# finished, Claude pushes the web commit, and then  git pull --ff-only  and -Part web. From the web part on, the
# folder may be at that later commit, as long as nothing the API is built from (app\, the code under scripts\, the
# requirements, the Dockerfile) changed since the release commit.
# Timing: the release changes engine files, so from the push until -Part api has finished /yq-load refuses. Start
# after a morning load and go from -Part backup to -Part api in one sitting.
#
# Before every step that writes to production (or runs on it) the script asks
#     Type YES to continue
# and anything else stops the part there (in the rollback: skips that piece). The first failure stops the part
# and says what failed. Everything shown on screen also goes to business_data\releases\<date>_r9.log; anything
# that looks like a password, key or token is masked first, and no secret is ever printed.
# business_data\releases\r9_state.json keeps what a later part needs: the encrypted backup (its archive), the gate
# snapshot taken with it, the API deploy that was live before the release (the rollback target) and how far the
# train got (migrate2 runs only once verify passed for the API that is live).
#
# The backup is  python -m scripts.yq_backup release r9  (a later attempt: r9-2, r9-3 ...): every table, checked,
# encrypted and read back under %LOCALAPPDATA%\yq-backups\release (never under business_data, never in OneDrive), the
# plain copy deleted; its last line is the archive. A table restore unpacks it outside OneDrive first (yq_backup
# unpack, whose last line is the folder it checked), only on the explicit word  -RestoreTables <list> .
#
# Rollback order, by who reads what: the v_transfer_flow drop is reversed first (the older API reads that view),
# then the API goes back, and only then the team_messages and stock-moves reverses (only the release API reads
# those). The narration mask cannot be undone: the stored narrations stay masked.
#
# It never pushes to GitHub, never deploys the web, never changes a Render or Cloudflare setting, never deletes the
# load lock or a backup, and never loads Focus files.
#
# Exit codes: 0 done, 1 failed (stop and tell Claude), 2 stopped (no YES, or something to do first).

param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('check', 'backup', 'migrate', 'api', 'web', 'verify', 'migrate2', 'rollback')]
  [string]$Part,
  [string]$RestoreTables = ''
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
$LOG     = Join-Path $RELDIR ($DAY + '_r9.log')
$STATE   = Join-Path $RELDIR 'r9_state.json'
$ENVFILE = Join-Path $ROOT '.env'
$LOCK    = Join-Path $ROOT 'business_data\ops\LOCK'
$GATES   = Join-Path $ROOT 'business_data\gates'
# The migrations. -Part migrate, before the API deploy, in this order: r9_stock_moves (the view v_transfer_lines,
# v_salesman_stock_recon rebuilt from the ledger, the narration mask), then r9_team_messages (the table team_messages,
# which only the API reads: the Command Centre of the admin and management logins). -Part migrate2, once -Part verify
# passed: r9_transfer_flow_drop (v_transfer_flow goes). The rollback reverses them the other way round: the drop first,
# then team_messages, then stock moves.
$R9_MOVES     = 'scripts\r9_stock_moves_migration.sql'
$R9_TEAM      = 'scripts\r9_team_messages_migration.sql'
$R9_DROP      = 'scripts\r9_transfer_flow_drop_migration.sql'
$R9_DROP_REV  = 'scripts\r9_transfer_flow_drop_reverse.sql'
$R9_TEAM_REV  = 'scripts\r9_team_messages_reverse.sql'
$R9_MOVES_REV = 'scripts\r9_stock_moves_reverse.sql'

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
  $s = $s -replace '\bgithub_pat_[A-Za-z0-9_]{16,}', 'github_pat_***'
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

function Select-ApiImagePaths([string[]]$names) {
  # The paths among $names that the API image is built from (the Dockerfile copies app\, scripts\ and the
  # requirements). A PowerShell script or a page of notes under scripts\ is never run by the API: it does not count.
  foreach ($n in $names) {
    $p = (([string]$n).Trim()) -replace '\\', '/'
    if (-not $p) { continue }
    if ($p -match '\.(ps1|md)$') { continue }
    if ($p -eq 'Dockerfile' -or $p -eq 'requirements.txt' -or $p -eq 'requirements.lock' -or $p.StartsWith('app/') -or $p.StartsWith('scripts/')) { $p }
  }
}

function Test-LaterCommit([string]$base) {
  # true when HEAD is $base or a later commit on top of it
  if (-not $base) { return $false }
  [void](Invoke-Git @('merge-base', '--is-ancestor', $base, 'HEAD'))
  return ($script:GitCode -eq 0)
}

function Get-ApiImageChanges([string]$base) {
  # the files the API image is built from that changed between $base and HEAD
  $names = Invoke-Git @('diff', '--name-only', $base, 'HEAD')
  if ($script:GitCode -ne 0) { return @('(git diff ' + $base + '..HEAD failed)') }
  return @(Select-ApiImagePaths ($names -split "`n"))
}

function Test-MovesPass([string[]]$lines) {
  # moves portal's verdict: a line with the word PASS and none with the word FAIL (a banner's LAST LOAD FAILED is
  # not the comparison's verdict)
  $pass = $false
  foreach ($l in $lines) {
    if (([string]$l) -cmatch '\bFAIL\b') { return $false }
    if (([string]$l) -cmatch '\bPASS\b') { $pass = $true }
  }
  return $pass
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

function Get-Newest([string]$dir, [string]$filter) {
  if (-not (Test-Path -LiteralPath $dir)) { return $null }
  $items = Get-ChildItem -LiteralPath $dir -Filter $filter -File -ErrorAction SilentlyContinue
  return ($items | Sort-Object LastWriteTime | Select-Object -Last 1)
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

function Assert-Ready([switch]$NewTrain, [switch]$AfterApi, [switch]$Lenient) {
  # Every part: branch main, at origin/main, no changed tracked file, no Focus load running, .env present, and the
  # commit of -Part backup. Check and backup start a new train; from the web part on (-AfterApi) a later commit is
  # fine when nothing the API is built from changed since the release commit (the web commit); the rollback notes.
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
    } elseif ($AfterApi) {
      if (-not (Test-LaterCommit $was)) {
        Stop-Part 2 ('This folder (' + $script:HEAD.Substring(0, 12) + ') is not a later commit of the release commit ' + $was.Substring(0, 12) + '. Tell Claude.')
      }
      $moved = @(Get-ApiImageChanges $was)
      if ($moved.Count -gt 0) {
        foreach ($f in $moved) { Say ('  changed since the release commit: ' + $f) }
        Stop-Part 2 'Files the API is built from changed since the release commit (listed above), and the live API does not have them. Tell Claude (a new train: -Part check and -Part backup for the new commit).'
      }
      Say ('  The release commit is ' + $was.Substring(0, 12) + '; this folder is at the later ' + $script:HEAD.Substring(0, 12) + ', with nothing the API is built from changed (the web commit).')
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
  foreach ($f in @($R9_MOVES, $R9_MOVES_REV, $R9_TEAM, $R9_TEAM_REV, $R9_DROP, $R9_DROP_REV,
                   'scripts\ops\pairing.py', 'scripts\ops\moves.py', 'scripts\ops\moves_portal.py', 'app\transfer_audit.py',
                   'scripts\yq_team_post.py',
                   'scripts\yq_backup.py', 'scripts\apply_sql.py', 'scripts\audit_grants.py', 'scripts\db_backup.py',
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
  if ((Get-GhHeaders).Count -gt 0) {
    Say '  ok GH_READ_TOKEN (GitHub is read with it; the value is never shown)'
  } else {
    Say '  GH_READ_TOKEN is not set: fine while the repository is public (once it is private, the script asks you to confirm CI by hand)'
  }
  Say '  -Part backup needs the backup password and 7-Zip, as the pre-load backups do; yq_backup says so if one is missing.'

  Step 'python'
  if ((Invoke-Py @('--version')) -ne 0) { Fail 'python --version' }

  Step 'GitHub: ci must be green for this commit'
  $w = Get-Runs $script:HEAD
  if ($null -eq $w) {
    Say ('  GitHub could not be read (no internet, or the repository is private and GH_READ_TOKEN is missing or has expired). Open https://github.com/' + $REPO + '/actions and look at the "ci" run of commit ' + $script:HEAD.Substring(0, 7) + '.') Yellow
    $a = Read-Host 'Type YES if ci shows a green tick for that commit'
    if (([string]$a).Trim() -ne 'YES') { Stop-Part 2 'Stopped: ci was not confirmed.' }
    Add-LogLine 'ci confirmed by hand'
  } else {
    $ci = Find-Run $w 'ci'
    Say ('  ci: ' + (Show-Run $ci))
    if ($null -eq $ci) { Stop-Part 2 'GitHub shows no ci run for this commit yet: wait a minute, then run -Part check again.' }
    if ([string]$ci.status -ne 'completed') { Stop-Part 2 'ci is still running (5-10 minutes after the push): wait until it is green, then run -Part check again.' }
    if ([string]$ci.conclusion -ne 'success') { Stop-Part 1 ('ci FAILED on this commit (' + [string]$ci.html_url + '). Do not release: tell Claude.') }
    Say '  ci is green.'
  }

  Step 'what is live now (reads only)'
  if ((Invoke-Py @('-m', 'scripts.render_deploy', 'status')) -ne 0) { Fail 'render_deploy status (is RENDER_API_KEY right?)' }
  Say ('  API /health commit:  ' + (Get-HealthCommit))
  Say ('  marketplace build:   ' + (Get-MarketBuild))
  Say ('  Bahrain time now:    ' + (Get-Date -Format 'ddd dd-MMM HH:mm') + '  (after the morning load, never during a Focus load)')
  Say '  The release changes engine files: until -Part api has finished, /yq-load refuses. Go from -Part backup to -Part api in one sitting.' Yellow
  Set-State 'checked' $script:HEAD
  Say 'Check passed. Next: -Part backup' Green
}
#endregion part:check

#region part:backup
function Invoke-PartBackup {
  # Reads production only. About 5-10 minutes, one YES.
  Assert-Ready -NewTrain
  $st = Get-State
  if ([string]$st.checked -ne $script:HEAD) { Stop-Part 2 'Run -Part check first, for this commit (ci green, the release files, the keys).' }
  # The FIRST backup and before-snapshot of this release stay the rollback point: a later run (the same commit
  # again, or a fix pushed after a partial release) is recorded as the latest and never replaces them.
  $kept = [string]$st.backup_archive
  $hasFirst = $kept -and (Test-Path -LiteralPath $kept -PathType Leaf)
  if ($hasFirst) { Say ('  The first backup of this release (' + $kept + ') stays the rollback point; this one is recorded as the latest.') Yellow }

  # Each attempt takes a label of its own (r9, then r9-2, r9-3 ...): yq_backup refuses a second backup of one label
  # on one day, and a run that stopped half-way leaves its folder behind. The attempt is counted once it starts.
  $runs = 1
  if ([string]$st.backup_runs -match '^\d+$') { $runs = [int]$st.backup_runs + 1 }
  $label = 'r9'
  if ($runs -gt 1) { $label = 'r9-' + $runs }

  Step ('backup of every table, encrypted outside OneDrive (yq_backup release ' + $label + '), + the before-snapshot (gate)')
  Confirm-Step ('Back up every table of the live database (python -m scripts.yq_backup release ' + $label + ': checked, encrypted, read back, the plain copy deleted; outside OneDrive) and take the gate snapshot pre-r9. It only READS production (about 5-10 minutes).')
  Set-State 'backup_runs' $runs
  $code = (Invoke-Py @('-m', 'scripts.yq_backup', 'release', $label))
  if ($code -eq 2) { Stop-Part 2 'yq_backup refused (its reason is above): nothing was made. If it says this backup is already there, run -Part backup again (the next run takes a new label); otherwise tell Claude.' }
  if ($code -ne 0) { Fail ('yq_backup release ' + $label + ' did not finish (its last line says what is left plain: ' + (Get-LastLine $script:LastOutput) + ')') }
  $arc = Get-LastLine $script:LastOutput
  if (-not $arc -or -not (Test-Path -LiteralPath $arc -PathType Leaf)) { Fail ('yq_backup did not end with the path of its archive (its last line: ' + $arc + ')') }
  if ((Invoke-Py @('-m', 'scripts.prod_gate', 'snapshot', '--label', 'pre-r9')) -ne 0) { Fail 'gate snapshot pre-r9 (it exits 1 when foreign-key orphans exist)' }
  $g = Get-Newest $GATES '*_pre-r9.json'
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
function Invoke-Migration([string]$label, [string]$file, [string]$what) {
  # rehearse (runs on production, always rolled back) -> apply (one transaction) -> grant audit
  Step ('migration ' + $label + ': ' + $file)
  Confirm-Step ('REHEARSE ' + $label + ' on production: every statement runs, then it is ROLLED BACK; nothing is kept.')
  if ((Invoke-Py @('-m', 'scripts.apply_sql', '--rehearse', $file)) -ne 0) { Fail ('the rehearsal of ' + $label + ' (nothing was changed)') }
  Confirm-Step ('APPLY ' + $label + ' to production: ' + $what + ' One transaction; it waits at most 2 s for a lock, else it fails with nothing applied.')
  if ((Invoke-Py @('-m', 'scripts.apply_sql', $file)) -ne 0) { Fail ('the apply of ' + $label + ' (one transaction: nothing of this file was kept); at a quieter moment run this part again') }
  if ((Invoke-Py @('-m', 'scripts.audit_grants')) -ne 0) { Fail ('audit_grants after ' + $label + ': something is open to the public key') }
  Set-State ('migrated_' + $label) $script:HEAD
}

function Invoke-PartMigrate {
  # About 3 minutes, four YES.
  Assert-Ready
  $arc = [string](Get-State).backup_archive
  if (-not $arc -or -not (Test-Path -LiteralPath $arc -PathType Leaf)) { Stop-Part 2 'There is no backup of this release yet: run -Part backup first.' }
  Say ('  backup of this release: ' + $arc)
  Invoke-Migration 'r9_stock_moves' $R9_MOVES 'the new view v_transfer_lines, v_salesman_stock_recon rebuilt from the ledger (its old columns kept) and the ID numbers in the stored stock narrations masked. The mask cannot be undone; the backup keeps the old text, encrypted.'
  Invoke-Migration 'r9_team_messages' $R9_TEAM 'the table team_messages (the team message behind the Copy button in the Command Centre; only the API reads it).'
  Say 'Both migrations are applied and the grant audit is clean. Next: -Part api' Green
}
#endregion part:migrate

#region part:api
function Invoke-PartApi {
  # 5-10 minutes, one YES.
  Assert-Ready
  $st = Get-State
  if ([string]$st.migrated_r9_stock_moves -ne $script:HEAD -or [string]$st.migrated_r9_team_messages -ne $script:HEAD) {
    Stop-Part 2 'The two migrations of this commit are not applied yet (the new API reads v_transfer_lines and team_messages): run -Part migrate first.'
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
  Set-State 'api_sha' $script:HEAD
  Set-State 'api_live' (Get-Date -Format 's')
  Say 'The API runs the release. Next: tell Claude, who pushes the web commit now; when Claude says it is pushed: git pull --ff-only, then -Part web' Green
}
#endregion part:api

#region part:web
function Invoke-PartWeb {
  # Reads only: the web commit, pushed by Claude once the API is live, deployed itself (cf-deploy, after ci). 1-15
  # minutes.
  Assert-Ready -AfterApi
  $st = Get-State
  $rel = [string]$st.release_sha
  if (-not $rel -or -not (Test-SameCommit ([string]$st.api_sha) $rel)) { Stop-Part 2 'The API of this release is not live yet: run -Part api first (the web goes live after the API).' }
  $sha = $script:HEAD

  Step 'the cf-deploy run of this commit'
  $w = Get-Runs $sha
  if ($null -eq $w) {
    Say ('  GitHub could not be read (no internet, or the repository is private and GH_READ_TOKEN is missing or has expired). Open https://github.com/' + $REPO + '/actions: the "cf-deploy" run of commit ' + $sha.Substring(0, 7) + ' must be green.') Yellow
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
    # the web of this release: the build must be of the release commit or of a later one (the web commit)
    [void](Invoke-Git @('merge-base', '--is-ancestor', $rel, $sha))
    if ($script:GitCode -ne 0) {
      Stop-Part 2 ('The newest web build (' + $sha.Substring(0, 12) + ') comes before the release commit: the web of this release is not pushed yet. Claude pushes it once the API is live; when Claude says so: git pull --ff-only, then -Part web again.')
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
      Stop-Part 1 'cf-deploy did not succeed: the previous web is still live (it works with the new API). When ci is green, on GitHub: Actions -> cf-deploy -> Run workflow (branch main), then -Part web again.'
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
    Say ('  Open ' + $PORTAL + '/stock in a private window: the amber "Being corrected" notice must be gone.') Yellow
    $a = Read-Host 'Type YES if it is gone'
    if (([string]$a).Trim() -ne 'YES') { Fail 'the portal does not show the new Stock Movement page' }
    Add-LogLine 'portal confirmed by hand'
  }
  Set-State 'web_live' $sha
  Say 'The new portal and marketplace are live. Next: -Part verify' Green
}
#endregion part:web

#region part:verify
function Invoke-PartVerify {
  # Reads only. About 3 minutes, then your checks in the portal.
  Assert-Ready -AfterApi
  $st = Get-State
  $api = [string]$st.api_sha
  if (-not $api -or -not (Test-SameCommit $api ([string]$st.release_sha))) { Stop-Part 2 'The API of this release is not live yet: run -Part api first.' }
  if (-not [string]$st.web_live) { Say '  Note: -Part web has not passed yet. The comparison below reads the API, so it runs all the same.' Yellow }

  Step 'the API runs the release'
  $hc = Get-HealthCommit
  Say ('  API /health commit: ' + $hc)
  if (-not (Test-SameCommit $hc $api)) { Fail 'the API does not run the release commit: run -Part api' }

  Step 'the portal pairs the transfers as /yq-moves does (moves portal must print PASS)'
  $movesOk = $false
  if ((Invoke-Py @('-m', 'scripts.ops', 'moves', 'portal')) -eq 0) { $movesOk = Test-MovesPass $script:LastOutput }
  if (Test-OutputHas '^(LOAD INTERRUPTED|LAST LOAD FAILED)') { Say '  The ops engine says a Focus load is not whole (above): after this part, type /yq-load in Claude Code, in this folder, to settle it.' Yellow }
  if ($movesOk) { Say '  moves portal: PASS' Green } else { Say '  moves portal did not print PASS (above). The gate compare runs all the same; this part fails after it.' Red }

  Step 'gate snapshot after the release, compared with the one taken at -Part backup'
  if ((Invoke-Py @('-m', 'scripts.prod_gate', 'snapshot', '--label', 'post-r9')) -ne 0) { Fail 'gate snapshot post-r9' }
  $pre = [string]$st.gate_pre
  if (-not $pre -or -not (Test-Path -LiteralPath (Join-Path $ROOT $pre))) {
    $g = Get-Newest $GATES '*_pre-r9.json'
    if ($g) { $pre = 'business_data\gates\' + $g.Name } else { $pre = '' }
  }
  if (-not $pre) { Fail 'there is no pre-r9 gate snapshot (run -Part backup first)' }
  $post = Get-Newest $GATES '*_post-r9.json'
  if (-not $post) { Fail 'the post-r9 snapshot file was not found' }
  if ((Invoke-Py @('-m', 'scripts.prod_gate', 'compare', $pre, ('business_data\gates\' + $post.Name))) -ne 0) {
    Fail 'gate compare: a pre-existing order, line or total changed without an explanation. Stop rule: tell Claude now (docs\RELEASE.md, Rollback)'
  }
  if (-not $movesOk) { Fail 'moves portal did not print PASS: the portal does not pair the transfers as /yq-moves does. Do not run -Part migrate2; tell Claude' }
  Set-State 'verified' $api

  Step 'your checks in the portal (about 5 minutes; nothing here is automatic)'
  $checks = @(
    ('  1. Stock Movement (' + $PORTAL + '/stock): the amber "Being corrected" notice is gone, the transfers show'),
    '     the new statuses (paired by their contents) and each van card shows its stock from the ledger.',
    ('  2. Command Centre (' + $PORTAL + '/command): the team message with its Copy button. It fills after the next'),
    '     /yq-load.',
    '  3. /yq-moves in Claude Code, the last 7 days: the same pairs as the page.'
  )
  foreach ($l in $checks) { Say $l }
  Say 'Verified. Next: -Part migrate2 (it drops the old v_transfer_flow view).' Green
}
#endregion part:verify

#region part:migrate2
function Invoke-PartMigrate2 {
  # Only once -Part verify passed for the API that is live. About 2 minutes, two YES.
  Assert-Ready -AfterApi
  $st = Get-State
  $api = [string]$st.api_sha
  if (-not $api -or -not (Test-SameCommit $api ([string]$st.release_sha)) -or -not (Test-SameCommit ([string]$st.verified) $api)) {
    Stop-Part 2 'The release is not verified yet: run -Part verify first (moves portal must print PASS and the gate compare must pass).'
  }

  Step 'the API runs the release (an older API reads v_transfer_flow; this one does not)'
  $hc = Get-HealthCommit
  Say ('  API /health commit: ' + $hc)
  if (-not (Test-SameCommit $hc $api)) { Stop-Part 2 'The API does not run the release now, so v_transfer_flow stays (an older API reads it). Tell Claude.' }
  Invoke-Migration 'r9_transfer_flow_drop' $R9_DROP 'the old view v_transfer_flow is dropped (only an older API read it; its reverse puts it back).'
  Say 'v_transfer_flow is dropped and the grant audit is clean. The release is complete: the after-release list in docs\RELEASE.md (Release 2b).' Green
}
#endregion part:migrate2

#region part:rollback
function Undo-Migration([string]$label, [string]$file, [string]$whenRefused) {
  # rehearse the reverse (rolled back) -> apply -> grant audit; a refusal changes nothing and is explained
  Step ('database: the ' + $label + ' reverse (' + $file + ')')
  if (-not (Ask-Yes ('REHEARSE the ' + $label + ' reverse on production (rolled back, nothing is kept). Anything else leaves ' + $label + ' as it is.'))) {
    Say ('  ' + $label + ' stays.') Yellow
    return $false
  }
  if ((Invoke-Py @('-m', 'scripts.apply_sql', '--rehearse', $file)) -ne 0) {
    Say ('  The ' + $label + ' reverse refused or failed in the rehearsal (above); nothing was changed. ' + $whenRefused) Yellow
    return $false
  }
  if (-not (Ask-Yes ('APPLY the ' + $label + ' reverse to production.'))) {
    Say ('  ' + $label + ' stays.') Yellow
    return $false
  }
  if ((Invoke-Py @('-m', 'scripts.apply_sql', $file)) -ne 0) { Fail ('the ' + $label + ' reverse (one transaction: nothing of it was kept)') }
  if ((Invoke-Py @('-m', 'scripts.audit_grants')) -ne 0) { Fail ('audit_grants after the ' + $label + ' reverse') }
  Set-State ('reversed_' + $label) (Get-Date -Format 's')
  return $true
}

function Invoke-PartRollback {
  # Only when something is wrong after the release. Order, by who reads what: R1 v_transfer_flow back (an older API
  # reads it) -> R2 the API back -> R3 the team_messages and stock-moves reverses (only the release API reads them)
  # -> R4 a table restore (last resort, only with -RestoreTables, exactly as Claude gives the list) -> R5 the gate
  # compare. Each piece asks YES; anything else skips that piece.
  Assert-Ready -Lenient
  $st = Get-State
  Say 'Rollback order: v_transfer_flow back, then the API, then (only when Claude says so) the other two reverses, then a restore only with -RestoreTables. Each piece asks for YES; anything else skips it.' Yellow
  Say '  The web stays: the new Stock Movement page and Command Centre work with an older API too. Should the new portal itself misbehave, docs\RELEASE.md (Rollback, Web) has the switch back; tell Claude.'

  Step 'R1. database: v_transfer_flow back (the r9_transfer_flow_drop reverse), before the API goes back: an older API reads it'
  if ([string]$st.migrated_r9_transfer_flow_drop) {
    Say '  -Part migrate2 dropped it: put it back before the API goes back.'
  } else {
    Say '  The state does not show -Part migrate2 applied: the view should still be there, and its reverse then changes nothing.'
  }
  $okD = Undo-Migration 'r9_transfer_flow_drop' $R9_DROP_REV 'Tell Claude before the API goes back: an older API reads v_transfer_flow.'

  Step 'R2. API (Render): back to the deploy that was live before the release'
  if ((Invoke-Py @('-m', 'scripts.render_deploy', 'status')) -ne 0) { Fail 'render_deploy status' }
  $dep = [string]$st.api_previous_deploy
  if ($dep) { Say ('  -Part api wrote down: ' + $dep + ' (commit ' + [string]$st.api_previous_commit + ')') }
  $typed = ([string](Read-Host ('Deploy id to roll back to (Enter = ' + $dep + ')'))).Trim()
  if ($typed) { $dep = $typed }
  Add-LogLine ('deploy id for the API rollback: ' + $dep)
  if ([string]$st.migrated_r9_transfer_flow_drop -and -not $okD -and -not [string]$st.reversed_r9_transfer_flow_drop) {
    Say '  Warning: v_transfer_flow is not back (R1 above), and an older API reads it: its Stock Movement page fails until it is. Roll the API back only if Claude says so.' Yellow
  }
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

  Step 'R3. database: the team_messages and stock-moves reverses (only when Claude says so; only the release API reads them)'
  $okT = $false
  $okS = $false
  $hc = Get-HealthCommit
  if ([string]$st.api_sha -and (Test-SameCommit $hc ([string]$st.api_sha))) {
    Say ('  The API still runs the release (' + $hc + '): it reads v_transfer_lines and team_messages, so both reverses wait until the API is back (R2).') Yellow
  } else {
    Say '  The narration mask cannot be undone: the stored stock narrations stay masked; these reverses put the old views back and drop the new table only.' Yellow
    $okT = Undo-Migration 'r9_team_messages' $R9_TEAM_REV 'Leave it: an older API ignores team_messages. Tell Claude.'
    $okS = Undo-Migration 'r9_stock_moves' $R9_MOVES_REV 'Leave it: an older API reads v_salesman_stock_recon by its old columns, which r9 kept. Tell Claude.'
  }
  Add-LogLine ('reverses: r9_transfer_flow_drop ' + $okD + ', r9_team_messages ' + $okT + ', r9_stock_moves ' + $okS)

  Step 'R4. last resort, only with Claude: tables from the encrypted backup taken before the release'
  if (-not $RestoreTables) {
    Say '  No -RestoreTables given: no table is restored. A restore is the last resort: run this part again with -RestoreTables <list>, exactly as Claude gives the list.'
  } else {
    $tables = $RestoreTables.Trim()
    Add-LogLine ('tables given for the restore: ' + $tables)
    if ($tables -cnotmatch '^[a-z_][a-z0-9_]*(,[a-z_][a-z0-9_]*)*$') { Fail ('not a table list: ' + $tables) }
    $arc = [string]$st.backup_archive
    if (-not $arc -or -not (Test-Path -LiteralPath $arc -PathType Leaf)) { Fail 'the encrypted backup of this release was not found (the state names none, or the file is gone)' }
    # a new folder next to the archive, in the backup store: yq_backup refuses one in OneDrive or the project folder
    $scratch = Join-Path (Split-Path -Parent $arc) ([IO.Path]::GetFileNameWithoutExtension($arc) + '_unpacked_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
    Say ('  backup: ' + $arc)
    Say ('  unpacked to: ' + $scratch + ' (outside OneDrive; it holds the backup unencrypted: Claude deletes it once the restore is done)')
    Say '  The backup was taken before the narration mask: restoring stock_movements would bring the unmasked narrations back.'
    Say '  The restore refuses by itself when live rows are newer than the backup (orders since the release): it never deletes them.'
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

  Step 'R5. gate snapshot after the rollback, compared with the one before the release'
  if ((Invoke-Py @('-m', 'scripts.prod_gate', 'snapshot', '--label', 'post-r9-rollback')) -ne 0) { Fail 'gate snapshot post-r9-rollback' }
  $pre = [string]$st.gate_pre
  $post = Get-Newest $GATES '*_post-r9-rollback.json'
  if ($pre -and $post -and (Test-Path -LiteralPath (Join-Path $ROOT $pre))) {
    if ((Invoke-Py @('-m', 'scripts.prod_gate', 'compare', $pre, ('business_data\gates\' + $post.Name))) -ne 0) { Fail 'gate compare after the rollback: tell Claude now' }
  } else {
    Say '  No pre-r9 snapshot to compare with: tell Claude.' Yellow
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
if ($RestoreTables -and $Part -ne 'rollback') { Stop-Part 2 '-RestoreTables belongs to -Part rollback only: nothing was done.' }
try {
  switch ($Part) {
    'check'    { Invoke-PartCheck }
    'backup'   { Invoke-PartBackup }
    'migrate'  { Invoke-PartMigrate }
    'api'      { Invoke-PartApi }
    'web'      { Invoke-PartWeb }
    'verify'   { Invoke-PartVerify }
    'migrate2' { Invoke-PartMigrate2 }
    'rollback' { Invoke-PartRollback }
  }
} catch {
  Say ('UNEXPECTED ERROR: ' + $_.Exception.Message) Red
  Stop-Part 1 ('Stopped on an unexpected error (above). Tell Claude (log: ' + $LOG + ').')
}
Stop-Part 0 ('Part ' + $Part + ' finished. Log: ' + $LOG)
