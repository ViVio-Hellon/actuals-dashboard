' symnavi_dde.vbs
' Drive SymNavi through DDE using Excel.Application (VBScript itself has no DDE).
' Usage: cscript //Nologo symnavi_dde.vbs <ddeApp> <ddeTopic> <rnePath> <xlsPath> <startupWaitSec> <ddeTimeoutSec> [sheetName]
' Exit codes: 0 ok / 2 usage / 3 rne missing / 4 dde connect failed / 5 dde command failed / 6 output missing
'
' The legacy VBA did: [Open(rne)] [Save(xls,"BIFF")] [Close()], then
'   "If ActiveWorkbook.Name <> ThisWorkbook.Name Then ActiveWorkbook.SaveAs OneTimePath"
' i.e. SymNavi may hand the result to Excel as an unsaved workbook instead of writing the file.
' So after Save we watch, every second, for any of:
'   (1) the xls file appearing and its size settling
'   (2) a workbook arriving in our own (hidden) Excel instance
'   (3) an unsaved workbook with sheet <sheetName> arriving in the user's running Excel
' and send [Close()] only after the data has been taken (or on timeout).
' Output lines are parsed by symnavi.py (keep the keywords). Keep this file ASCII only.
Option Explicit

Dim args: Set args = WScript.Arguments
If args.Count < 6 Then
  WScript.Echo "usage: symnavi_dde.vbs ddeApp ddeTopic rnePath xlsPath startupWaitSec ddeTimeoutSec [sheetName]"
  WScript.Quit 2
End If
Dim ddeApp, ddeTopic, rnePath, xlsPath, startupWait, ddeTimeout, sheetName
ddeApp = args(0): ddeTopic = args(1): rnePath = args(2): xlsPath = args(3)
startupWait = CLng(args(4)): ddeTimeout = CLng(args(5))
sheetName = "Page1"
If args.Count >= 7 Then sheetName = args(6)

Dim fso: Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FileExists(rnePath) Then
  WScript.Echo "rne missing: " & rnePath
  WScript.Quit 3
End If
If fso.FileExists(xlsPath) Then fso.DeleteFile xlsPath, True

Dim xl: Set xl = CreateObject("Excel.Application")
xl.Visible = False
xl.DisplayAlerts = False
' Tell Python which Excel is ours, so it can be closed even if this script is stopped
On Error Resume Next
WScript.Echo "excel hwnd=" & xl.Hwnd
Err.Clear
On Error GoTo 0

' --- connect (SymNavi may still be starting / logging in) ---
Dim ch, tStart, connected
connected = False
tStart = Timer
Do
  On Error Resume Next
  ch = xl.DDEInitiate(ddeApp, ddeTopic)
  If Err.Number = 0 And ch > 0 Then connected = True
  Err.Clear
  On Error GoTo 0
  If connected Then Exit Do
  If Elapsed(tStart) > startupWait Then Exit Do
  WScript.Echo "connect wait sec=" & Elapsed(tStart)
  WScript.Sleep 1000
Loop
If Not connected Then
  WScript.Echo "connect failed after " & startupWait & "s (" & ddeApp & "/" & ddeTopic & ")"
  QuitExcel
  WScript.Quit 4
End If
WScript.Echo "connect ok channel=" & ch & " sec=" & Elapsed(tStart)

' --- commands ---
On Error Resume Next
xl.DDEExecute ch, "[Open(""" & rnePath & """)]"
If Err.Number <> 0 Then
  WScript.Echo "cmd open error " & Err.Number & ": " & Err.Description
  Err.Clear
  xl.DDETerminate ch
  On Error GoTo 0
  QuitExcel
  WScript.Quit 5
End If
WScript.Echo "cmd open ok"
xl.DDEExecute ch, "[Save(""" & xlsPath & """, ""BIFF"")]"
If Err.Number <> 0 Then
  ' Excel may report a DDE timeout for a long Save while SymNavi keeps working: keep watching
  WScript.Echo "cmd save error " & Err.Number & ": " & Err.Description & " (keep waiting)"
  Err.Clear
Else
  WScript.Echo "cmd save ok"
End If
On Error GoTo 0

' --- watch for the data ---
Dim tWait, lastSize, curSize, stable, got, nOwn, nOther, sec, lastEcho
tWait = Timer: lastSize = -1: stable = 0: got = "": lastEcho = -5
Do
  curSize = -1
  If fso.FileExists(xlsPath) Then curSize = fso.GetFile(xlsPath).Size
  If curSize > 0 And curSize = lastSize Then stable = stable + 1 Else stable = 0
  lastSize = curSize
  If stable >= 2 Then got = "file": Exit Do

  nOwn = TakeFromOwnExcel()
  If nOwn = -1 Then got = "own-excel": Exit Do
  nOther = TakeFromOtherExcel()
  If nOther = -1 Then got = "other-excel": Exit Do

  sec = Elapsed(tWait)
  If sec > ddeTimeout Then Exit Do
  If sec - lastEcho >= 5 Then
    WScript.Echo "wait sec=" & sec & " limit=" & ddeTimeout & " file=" & curSize & " own_books=" & nOwn & " other_books=" & nOther
    lastEcho = sec
  End If
  WScript.Sleep 1000
Loop

' --- close the document in SymNavi and end the conversation ---
On Error Resume Next
xl.DDEExecute ch, "[Close()]"
Err.Clear
xl.DDETerminate ch
Err.Clear
On Error GoTo 0
QuitExcel

If got <> "" And fso.FileExists(xlsPath) Then
  If fso.GetFile(xlsPath).Size > 0 Then
    WScript.Echo "output ok via=" & got & ": " & xlsPath & " (" & fso.GetFile(xlsPath).Size & " bytes)"
    WScript.Quit 0
  End If
End If
WScript.Echo "output missing after " & Elapsed(tWait) & "s: " & xlsPath
WScript.Quit 6

' ======================================================================
Function Elapsed(t0)
  Dim d: d = Timer - t0
  If d < 0 Then d = d + 86400   ' past midnight
  Elapsed = Int(d)
End Function

' A workbook in our own hidden Excel can only have come from SymNavi.
' Returns the number of workbooks seen, or -1 when one was saved to xlsPath.
' (Inside a VBScript function, results are kept in a local variable: reading the
'  function's own name could be taken as a recursive call.)
Function TakeFromOwnExcel()
  Dim wb, n, res
  n = 0: res = 0
  On Error Resume Next
  n = xl.Workbooks.Count
  If Err.Number <> 0 Then n = 0: Err.Clear
  res = n
  If n > 0 Then
    For Each wb In xl.Workbooks
      If SaveBook(wb, "own Excel") Then res = -1: Exit For
    Next
  End If
  Err.Clear
  On Error GoTo 0
  TakeFromOwnExcel = res
End Function

' The user's running Excel: only an UNSAVED workbook that has sheet <sheetName> is taken
' (never touch the user's own files).
Function TakeFromOtherExcel()
  Dim ox, wb, n, res
  n = 0: res = 0
  On Error Resume Next
  Set ox = GetObject(, "Excel.Application")
  If Err.Number <> 0 Or Not IsObject(ox) Then
    Err.Clear: On Error GoTo 0
    TakeFromOtherExcel = 0
    Exit Function
  End If
  If ox.Hwnd = xl.Hwnd Then
    Err.Clear: On Error GoTo 0
    TakeFromOtherExcel = 0
    Exit Function
  End If
  For Each wb In ox.Workbooks
    If wb.Path = "" Then
      If HasSheet(wb, sheetName) Then
        n = n + 1
        If SaveBook(wb, "running Excel") Then res = -1: Exit For
      End If
    End If
  Next
  If res <> -1 Then res = n
  Err.Clear
  On Error GoTo 0
  TakeFromOtherExcel = res
End Function

Function HasSheet(wb, name)
  Dim ws, found
  found = False
  On Error Resume Next
  For Each ws In wb.Worksheets
    If LCase(ws.Name) = LCase(name) Then found = True: Exit For
  Next
  Err.Clear
  HasSheet = found
End Function

Function SaveBook(wb, where)
  Dim nm, prevAlerts, saveErr, saveDesc, ok
  ok = False
  On Error Resume Next
  nm = wb.Name
  prevAlerts = wb.Application.DisplayAlerts
  wb.Application.DisplayAlerts = False
  If fso.FileExists(xlsPath) Then fso.DeleteFile xlsPath, True
  Err.Clear
  wb.SaveAs xlsPath, 56          ' xlExcel8 (BIFF8 .xls)
  saveErr = Err.Number: saveDesc = Err.Description
  Err.Clear
  wb.Application.DisplayAlerts = prevAlerts   ' leave the user's Excel as it was
  If saveErr = 0 Then
    wb.Close False
    WScript.Echo "found workbook " & nm & " in " & where & " -> saved"
    ok = True
  Else
    WScript.Echo "found workbook " & nm & " in " & where & " but SaveAs failed " & saveErr & ": " & saveDesc
  End If
  Err.Clear
  SaveBook = ok
End Function

Sub QuitExcel()
  On Error Resume Next
  Dim wb
  For Each wb In xl.Workbooks
    wb.Close False
  Next
  xl.Quit
  Set xl = Nothing
  Err.Clear
  On Error GoTo 0
End Sub
