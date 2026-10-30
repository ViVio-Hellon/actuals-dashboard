' xls_to_tsv.vbs
' Read a BIFF (.xls) sheet through ACE OLEDB and write it as UTF-8 TSV (header row = column names).
' Usage: cscript //Nologo xls_to_tsv.vbs <xlsPath> <sheetName> <tsvPath>
' Exit codes: 0 ok / 2 usage / 3 xls missing / 4 open failed
' Note: the ACE provider must match cscript bitness. For 32-bit ACE use %SystemRoot%\SysWOW64\cscript.exe.
' Keep this file ASCII only.
Option Explicit

Dim args: Set args = WScript.Arguments
If args.Count < 3 Then
  WScript.Echo "usage: xls_to_tsv.vbs xlsPath sheetName tsvPath"
  WScript.Quit 2
End If
Dim xlsPath, sheetName, tsvPath
xlsPath = args(0): sheetName = args(1): tsvPath = args(2)

Dim fso: Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FileExists(xlsPath) Then
  WScript.Echo "xls not found: " & xlsPath
  WScript.Quit 3
End If

Dim cn: Set cn = CreateObject("ADODB.Connection")
On Error Resume Next
' IMEX=1 : treat mixed columns as text so leading zeros survive. HDR=Yes : first row = names.
cn.Open "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=" & xlsPath & ";Extended Properties=""Excel 8.0;HDR=Yes;IMEX=1"";"
If Err.Number <> 0 Then
  WScript.Echo "open failed " & Err.Number & ": " & Err.Description
  WScript.Quit 4
End If
Dim rs: Set rs = CreateObject("ADODB.Recordset")
rs.Open "SELECT * FROM [" & sheetName & "$]", cn, 0, 1
If Err.Number <> 0 Then
  WScript.Echo "query failed " & Err.Number & ": " & Err.Description
  cn.Close
  WScript.Quit 4
End If
On Error GoTo 0

Dim st: Set st = CreateObject("ADODB.Stream")
st.Type = 2: st.Charset = "utf-8": st.Open

Dim i, n, line
n = rs.Fields.Count
line = ""
For i = 0 To n - 1
  If i > 0 Then line = line & vbTab
  line = line & Clean(rs.Fields(i).Name)
Next
st.WriteText line, 1

Dim rows: rows = 0
Do Until rs.EOF
  line = ""
  For i = 0 To n - 1
    If i > 0 Then line = line & vbTab
    line = line & Clean(ToText(rs.Fields(i).Value))
  Next
  st.WriteText line, 1
  rows = rows + 1
  If rows Mod 500 = 0 Then WScript.Echo "progress rows=" & rows
  rs.MoveNext
Loop
rs.Close: cn.Close
st.SaveToFile tsvPath, 2
st.Close
WScript.Echo "rows=" & rows & " cols=" & n & " -> " & tsvPath
WScript.Quit 0

Function Clean(s)
  Clean = Replace(Replace(Replace(s, vbTab, " "), vbCr, " "), vbLf, " ")
End Function

Function ToText(v)
  If IsNull(v) Or IsEmpty(v) Then
    ToText = ""
  ElseIf VarType(v) = vbDate Then
    ToText = Year(v) & "-" & Pad2(Month(v)) & "-" & Pad2(Day(v)) & " " & Pad2(Hour(v)) & ":" & Pad2(Minute(v)) & ":" & Pad2(Second(v))
  Else
    ToText = CStr(v)
  End If
End Function

Function Pad2(x)
  Pad2 = Right("0" & CStr(x), 2)
End Function
