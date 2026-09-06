' start_coin_tray.vbs — launch Coin Tray from Stream Deck (no console window)
Option Explicit

Dim sh, fso, dir, script, cmd, found, py

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

dir = fso.GetParentFolderName(WScript.ScriptFullName)
script = dir & "\coin_desktop.py"
sh.CurrentDirectory = dir

If Not fso.FileExists(script) Then
  MsgBox "coin_desktop.py not found next to this launcher:" & vbCrLf & script, _
         vbCritical, "Coin Tray"
  WScript.Quit 1
End If

' Prefer a concrete pythonw so Stream Deck doesn't land on a bare install
' without Pillow/tkinterdnd2. Fall back through common locations.
found = False
cmd = ""

If Which("pyw.exe") Then
  ' Pin 3.13 if present (dev machine); else default pyw -3
  If fso.FileExists(sh.ExpandEnvironmentStrings( _
      "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe")) Then
    cmd = "pyw -3.13 """ & script & """"
    found = True
  ElseIf fso.FileExists(sh.ExpandEnvironmentStrings( _
      "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe")) Then
    cmd = "pyw -3.12 """ & script & """"
    found = True
  Else
    cmd = "pyw -3 """ & script & """"
    found = True
  End If
End If

If Not found Then
  For Each py In Array( _
      sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"), _
      sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"), _
      sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"), _
      "pythonw.exe", "python.exe")
    If InStr(py, "\") > 0 Then
      If fso.FileExists(py) Then
        cmd = """" & py & """ """ & script & """"
        found = True
        Exit For
      End If
    ElseIf Which(py) Then
      cmd = py & " """ & script & """"
      found = True
      Exit For
    End If
  Next
End If

If Not found Then
  MsgBox "No Python found on PATH (tried pyw, pythonw, python)." & vbCrLf & _
         "Install Python from python.org and tick 'Add to PATH'.", _
         vbCritical, "Coin Tray"
  WScript.Quit 1
End If

' 0 = hidden window, False = don't wait
sh.Run cmd, 0, False
WScript.Quit 0

Function Which(exe)
  Dim rc
  rc = sh.Run("cmd /c where " & exe & " >nul 2>&1", 0, True)
  Which = (rc = 0)
End Function
