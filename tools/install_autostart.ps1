<#
.SYNOPSIS
    install_autostart.ps1 - make Mr Odd Ball come up when Windows does.

.DESCRIPTION
    The Windows counterpart of tools/install_autostart.sh, and the same three verbs.

    On the Pi there were two pieces with genuinely different lifetimes: a systemd user unit
    for the assistant (audio, no screen, starts at boot via lingering) and an XDG autostart
    entry for the face (needs the Wayland session, so it can only start once the desktop is
    up). Windows collapses that distinction - shell:startup runs after logon, by which point
    there is always a desktop - so there is ONE piece here, and both processes are started by
    config/start_oddball.bat.

    That is a simplification, not a shortcut, and it costs one thing worth naming: the Pi's
    unit had `Restart=on-failure` with `RestartSec=5` and gave up after 3 failures in 5
    minutes. shell:startup has no such thing. If he falls over at 3am he stays down until the
    next logon. Task Scheduler can restart a task and is the upgrade path if that turns out to
    matter; it is deliberately not used yet, because a scheduled task is much harder for LB to
    see, disable, or reason about than a shortcut in a folder he can open.

    What gets installed is a SHORTCUT to config/start_oddball.vbs, never a copy. A copy would
    go stale the first time the repo is updated, and the failure mode of a stale copy is that
    `git pull` appears to change nothing.

.PARAMETER Action
    install   put the shortcut in shell:startup
    status    show what is installed and whether he is running
    remove    take it back out

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1 install
    powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1 status
    powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1 remove

.NOTES
    Author: LB   Date: 2026-08-26
    Run it from the repo root. It needs no administrator rights: shell:startup is per-user,
    which is correct - he needs this user's microphone, speaker and desktop, exactly as the
    Pi's unit was a USER unit and for the same reason.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'status', 'remove')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'

# Repo root is this script's directory minus \tools. Resolved rather than assumed, so the
# script works when invoked by full path from somewhere else.
$Repo = Split-Path -Parent $PSScriptRoot
$Vbs = Join-Path $Repo 'config\start_oddball.vbs'
$Bat = Join-Path $Repo 'config\start_oddball.bat'

# [Environment]::GetFolderPath('Startup') rather than a hardcoded AppData path: the folder is
# relocatable, and on this box OneDrive has already moved Documents and Desktop out from under
# %USERPROFILE%. Asking Windows where it is costs nothing and cannot drift.
$StartupDir = [Environment]::GetFolderPath('Startup')
$Link = Join-Path $StartupDir 'Mr Odd Ball.lnk'

function Say([string]$Text) { Write-Host "  $Text" }

function Get-OddballProcesses {
    <#
        Which of his processes are up. Matched on the COMMAND LINE, not the image name:
        every one of them is `python.exe` or `pythonw.exe`, so an image-name match would
        report every unrelated Python on the box as Mr Odd Ball.
    #>
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -and (
            $_.CommandLine -like '*main.py*--voice*' -or
            $_.CommandLine -like '*hud\float.py*' -or
            $_.CommandLine -like '*hud/float.py*') }
}

function Show-Status {
    Write-Host ''
    Write-Host '== autostart =='
    if (Test-Path $Link) {
        Say "installed: $Link"
        $sh = New-Object -ComObject WScript.Shell
        $sc = $sh.CreateShortcut($Link)
        Say "points to: $($sc.TargetPath) $($sc.Arguments)"
        # A shortcut that survived a repo move is a shortcut that no longer starts anything.
        if (-not (Test-Path $sc.TargetPath)) {
            Say 'WARNING:   its target does not exist - reinstall to repoint it'
        }
    }
    else {
        Say 'not installed'
    }

    Write-Host ''
    Write-Host '== running =='
    $procs = @(Get-OddballProcesses)
    if ($procs.Count -eq 0) {
        Say 'nothing running'
    }
    else {
        foreach ($p in $procs) {
            $what = if ($p.CommandLine -like '*float.py*') { 'face     ' } else { 'assistant' }
            Say "$what pid $($p.ProcessId)"
        }
    }

    Write-Host ''
    Write-Host '== log =='
    $log = Join-Path $Repo 'data\oddball.log'
    if (Test-Path $log) {
        Say "$log"
        # The last few lines only. The whole file is the wrong thing to dump into a terminal
        # somebody is using to answer "is he up?".
        # -Encoding utf8 is required, not cosmetic. The Python processes write UTF-8 (main.py
        # reconfigures stdout for it, because an EE copilot prints Ohm, micro and degree), and
        # Get-Content on PowerShell 5.1 defaults to the ANSI codepage — so an em-dash reads
        # back as "a-tilde-euro" and the log looks corrupted when it is not.
        Get-Content $log -Tail 5 -Encoding utf8 | ForEach-Object { Say "  | $_" }
    }
    else {
        Say 'no log yet'
    }
    Write-Host ''
}

function Install-Autostart {
    foreach ($f in @($Vbs, $Bat)) {
        if (-not (Test-Path $f)) {
            throw "missing $f - is this the repo root?"
        }
    }

    $sh = New-Object -ComObject WScript.Shell
    $sc = $sh.CreateShortcut($Link)

    # wscript.exe explicitly, rather than letting the .vbs be its own target. Two reasons: the
    # default handler for .vbs can be changed (some hardening guides repoint it to notepad,
    # which would silently OPEN the script every logon instead of running it), and naming the
    # host means the console-hiding behaviour is ours rather than a file association's.
    $sc.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
    $sc.Arguments = '"{0}"' -f $Vbs
    $sc.WorkingDirectory = $Repo
    $sc.Description = 'Mr Odd Ball - EE copilot. Starts the assistant and his face.'
    $sc.WindowStyle = 7          # minimised; the .vbs hides it outright, this is belt and braces
    $sc.Save()

    Write-Host ''
    Say "installed: $Link"
    Say "runs:      wscript.exe `"$Vbs`""
    Say ''
    Say 'He will start at your next logon. To start him NOW without logging out:'
    Say "    wscript `"$Vbs`""
    Say ''
    Say 'To check on him:'
    Say '    powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1 status'
    Write-Host ''
}

function Remove-Autostart {
    Write-Host ''
    if (Test-Path $Link) {
        Remove-Item $Link -Force
        Say "removed: $Link"
    }
    else {
        Say 'was not installed'
    }

    # Deliberately does NOT kill running processes. `remove` is about whether he starts next
    # time, and silently killing a running assistant mid-sentence is a different, larger
    # action than the one that was asked for. The count is printed so it is not a surprise.
    $procs = @(Get-OddballProcesses)
    if ($procs.Count -gt 0) {
        Say "$($procs.Count) process(es) still running - this does not stop them."
        Say "To stop him now:  Get-Process python,pythonw | Stop-Process"
    }
    Write-Host ''
}

switch ($Action) {
    'install' { Install-Autostart; Show-Status }
    'remove' { Remove-Autostart }
    'status' { Show-Status }
}
