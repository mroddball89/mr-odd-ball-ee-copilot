' =========================================================================================
'  start_oddball.vbs - run start_oddball.bat with no console window.
'  Author: LB    Date: 2026-08-26
'
'  THIS is the file that goes in shell:startup, not the .bat.
'
'  ## Why a second file exists at all
'
'  A batch file in shell:startup opens a console window and keeps it open for as long as the
'  processes it started are alive. `pythonw.exe` inside the .bat removes the console from the
'  two PYTHON processes; it does nothing about the console cmd.exe gives the batch file
'  itself. So a .bat alone leaves a black rectangle on the desktop all day, in front of the
'  transparent, always-on-top face this whole port exists to render cleanly.
'
'  `WScript.Shell.Run` with intWindowStyle = 0 is the standard Windows answer and needs
'  nothing installed: wscript.exe has shipped with every version since NT.
'
'  ## The arguments, and why each one is there
'
'    0      intWindowStyle - hide the window. This is the entire point of the file.
'    False  bWaitOnReturn  - return immediately rather than blocking until he exits. True
'                            would hold a wscript.exe process open for the whole session for
'                            no benefit, and would make logging out slower.
'
'  ## Deliberately NOT here
'
'  No error dialog on failure. A MsgBox at login is a modal box nobody is sitting in front of,
'  and it would block the rest of startup behind an OK button. Failures go to
'  data\oddball.log, which is where the .bat already writes and where you would look anyway.
'
'  No retry loop. If he fails to start, the answer is in the log, and a script that quietly
'  restarts a broken process forever is how a real fault becomes invisible.
' =========================================================================================

Option Explicit

Dim shell, fso, here, target

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' This script's own directory, so a shortcut to it works from anywhere. Resolving the path
' rather than assuming the working directory is what makes it safe to put a SHORTCUT in
' shell:startup instead of a copy - and a shortcut is what you want, so that a `git pull`
' updates the thing that actually runs.
here = fso.GetParentFolderName(WScript.ScriptFullName)
target = fso.BuildPath(here, "start_oddball.bat")

If Not fso.FileExists(target) Then
    ' Nothing to run and nowhere useful to say so - the log lives beside the batch file that
    ' is missing. Exit non-zero so Task Scheduler, if it is ever used instead of
    ' shell:startup, records a failure rather than a success.
    WScript.Quit 1
End If

' The quotes around target are required, not decorative: the repo path contains "OneDrive" and
' a space is one rename away at all times.
shell.Run """" & target & """", 0, False
