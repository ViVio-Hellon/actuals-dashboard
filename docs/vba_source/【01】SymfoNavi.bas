Attribute VB_Name = "【01】SymfoNavi"
Option Explicit
'既存RNEﾌｧｲﾙからEXCELﾌｧｲﾙ(.xls限定)を同一ﾌｫﾙﾀﾞに出力する
'※作成するEXCELﾌｧｲﾙはｾｷｭﾘﾃｨの制限によりVBAで開くことはできない。
Sub データ作成(RNEName As String, RNEPath As String, ID As String, PASS As String, TimeS As Double)
Application.ScreenUpdating = False
Application.DisplayAlerts = False

'Debug.Print "ID: " & ID
'Debug.Print "PASS: " & PASS

Dim NewFileName As String
Dim SaveFilePath As String
Dim TaskMENU As String
Dim TaskID As Variant
Dim Channel As Variant
Dim EndRow As Long
Dim EndCol As Long
Dim Symfo As Variant '一括読み込み用（2次元配列）

Const EXE As String = "C:\NAVICL\bin\SymNavi.exe" 'SymfoNavi.exeのｱﾄﾞﾚｽ
Const COM As String = "-d -u"                     '専用のｺﾏﾝﾄﾞ
Const SRV As String = "NlmFaNgyNavd"              'ｻｰﾊﾞｰ名

'<RNEﾌｧｲﾙ指定>起動ﾌｧｲﾙとRNEﾌｧｲﾙは同一ﾌｫﾙﾀﾞに保存すること
'Debug.Print "RNEName: " & RNEName
'Debug.Print "RNEPath: " & RNEPath
NewFileName = Left(RNEName, Len(RNEName) - 4) & ".xls"
SaveFilePath = ThisWorkbook.Path & "\" & NewFileName
 
  Application.StatusBar = "SymfoNaviを起動中"
  TaskMENU = "" & EXE & " " & COM & """" & ID & """" & "," & """" & PASS & """" & "," & """" & SRV & """"
  TaskID = Shell(TaskMENU, vbMinimizedFocus)
'  Debug.Print "TaskMENU: " & TaskMENU
  
  'DDEInitate：チャンネル番号を確認する（通信が失敗した場合は 0 を返す）
  Channel = DDEInitiate("SymNavi", "Macro") 'リンクの確立をする

  Application.StatusBar = "SymfoNaviとの接続を開始しました。"
  'DDE ダイナミック・データ・エクスチェンジ
  'DDEExecute:第一引数はDDEInitiate メソッドで開いたチャネルの番号を使う､第二引数はDDEコマンド
  'DDEExecute 命令で問題点は第二引数のコマンドが成功したかどうかの判断が出来ない｡

  DDEExecute Channel, "[Open(""" + RNEPath + """)]" 'SymNaviの[Open()]コマンドを実行（起動IDの入力画面）
  
  Application.StatusBar = "SymfoNavi作成データを保存中"
  DDEExecute Channel, "[Save(""" + SaveFilePath + """, ""BIFF"")]" ' SymNaviの[Save()]コマンドの実行
  DDEExecute Channel, "[Close()]" ' SymNaviの[Close()]コマンドの実行
  DDETerminate (Channel) ' DDE会話終了
  Application.StatusBar = "SymfoNaviとの接続を終了しました。"

  If ActiveWorkbook.Name <> ThisWorkbook.Name Then ActiveWorkbook.SaveAs SaveFilePath
  
  Application.StatusBar = "データを利用できる形に展開しています。"
  Workbooks.Open SaveFilePath
  With Sheets("Page1")
    EndRow = .Cells(Rows.Count, 1).End(xlUp).Row
    EndCol = .Cells(1, Columns.Count).End(xlToLeft).Column
    Symfo = Range(.Cells(1, 1), .Cells(EndRow, EndCol))
  End With
  Workbooks(NewFileName).Close
  Kill SaveFilePath 'ファイルを削除
  
  'SymfoNavi終了************************************
    Dim WD, task
    Set WD = CreateObject("Word.Application")
    For Each task In WD.Tasks
        If InStr(task.Name, "Navigator クライアント") > 0 Then
            task.Close
        End If
    Next
    WD.Quit
    Set WD = Nothing
  '*************************************************
  
  Application.StatusBar = "データをEXCELに貼り付けています。"
  With ThisWorkbook.Sheets("DATA")
    .Cells.Delete Shift:=xlUp
    Range(.Cells(1, 1), .Cells(EndRow, EndCol)) = Symfo
  End With
    
  Application.StatusBar = "ﾃﾞｰﾀのｸﾞﾙｰﾌﾟ分け処理を開始"
  Call グループ分け(True, TimeS) 'True：現場暦で集計,　False：太陽暦で集計

Application.DisplayAlerts = True
Application.ScreenUpdating = True
End Sub

