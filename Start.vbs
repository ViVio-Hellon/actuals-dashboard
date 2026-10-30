' Start.vbs - normal launcher (no console window). ASCII only.
' Starts start_app.py with pythonw.exe; Python opens the browser when the screen is ready.
' To pin a specific Python, write its full path into config\python_path.txt (e.g. C:\Python312\pythonw.exe).
Option Explicit
Dim fso, sh, root, py, f, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
py = "pythonw.exe"
If fso.FileExists(root & "\config\python_path.txt") Then
  Set f = fso.OpenTextFile(root & "\config\python_path.txt", 1)
  If Not f.AtEndOfStream Then py = Trim(f.ReadLine)
  f.Close
End If
sh.CurrentDirectory = root
cmd = Chr(34) & py & Chr(34) & " " & Chr(34) & root & "\start_app.py" & Chr(34)
On Error Resume Next
sh.Run cmd, 0, False
If Err.Number <> 0 Then
  MsgBox "Python could not be started: " & py & vbCrLf & Err.Description & vbCrLf & vbCrLf & _
         "Install Python 3.8+ or write the full path of pythonw.exe into config\python_path.txt", 16, "Actuals Dashboard"
End If
