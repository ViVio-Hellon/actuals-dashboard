' accdb_to_tsv.vbs
' Dump one table of an Access .accdb to UTF-8 TSV (used to migrate / verify existing files).
' Usage: cscript //Nologo accdb_to_tsv.vbs <accdbPath> <tableName> <tsvPath>
' Exit codes: 0 ok / 2 usage / 3 accdb missing / 4 open failed
' Keep this file ASCII only.
Option Explicit

Dim args: Set args = WScript.Arguments
If args.Count < 3 Then
  WScript.Echo "usage: accdb_to_tsv.vbs accdbPath tableName tsvPath"
  WScript.Quit 2
End If
Dim dbPath, tableName, tsvPath
dbPath = args(0): tableName = args(1): tsvPath = args(2)

Dim fso: Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FileExists(dbPath) Then
  WScript.Echo "accdb not found: " & dbPath
  WScript.Quit 3
End If

Dim cn: Set cn = CreateObject("ADODB.Connection")
On Error Resume Next
cn.Open "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=" & dbPath & ";"
If Err.Number <> 0 Then
  WScript.Echo "open failed " & Err.Number & ": " & Err.Description
  WScript.Quit 4
End If
Dim rs: Set rs = CreateObject("ADODB.Recordset")
rs.Open "SELECT * FROM [" & tableName & "]", cn, 0, 1
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
