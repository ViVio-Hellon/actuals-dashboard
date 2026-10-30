Attribute VB_Name = "【02】グループ分け"
Option Explicit
'表記     意味                型宣言文字  データ範囲 Byte数
'=====================================================================================================================================================
'Byte     バイト型                        0～255 1
'Boolean  ブール型                        TrueまたはFalse 2
'Integer  整数型              %           -32,768　～　32,767 2
'Long     長整数型            &           -2,147,483,648 ～ 2,147,483,647 4
'Currency 通貨型              @           -922,337,203,685,477.5808～　922,337,203,685,477.5807 8
'Single   単精度浮動小数点型  !           -3.402823E38～-1.401298E-45(負の値)1.401298E-45～3.402823E38(正の値) 4
'Double   倍精度浮動小数点型  #           -1.79769313486232E308～-4.94065645841247E-324(負の値)4.94065645841247E-324～1.79769313486232E308(正の値) 8
'Date     日付型                          西暦1900年1月1日～西暦9999年12月31日 8
'String   固定長文字列型      $           0～約64KB 1L+2L可変長文字列型  0～2GB 10+L
'Variant  バリアント型                    数値(Double)文字列(可変長文字列) 1622+L
'Object   オブジェクト型                  オブジェクトを参照する 4
'Decimal                                  10進   14
'=====================================================================================================================================================
Sub グループ分け(Gflag As Boolean, TimeS As Double) 'Gflag：集計日付選択用ﾌﾗｸﾞ , TimeS：時間計測用
Attribute グループ分け.VB_ProcData.VB_Invoke_Func = " \n14"
Application.ScreenUpdating = False
Application.Calculation = xlCalculationManual

Dim EndRow As Long '最終行判定
Dim MaxRow As Long '最終行判定
Dim flagV As Boolean '同時VCﾌﾗｸﾞ
Dim a As Long '汎用ｶｳﾝﾀ
Dim b As Long '汎用ｶｳﾝﾀ
Dim n(1 To 2) As Long '製造orｵｰﾀﾞｰ判断用
Dim Con As Variant '条件用2次元配列
Dim DB1 As Variant 'ﾃﾞｰﾀ用2次元配列
Dim DB2 As Variant 'ﾃﾞｰﾀ用2次元配列
Dim WSH As Object '自動で閉じるPopUpﾒｿｯﾄﾞ用(Windows Scripting Host)
Dim StartFlag As Boolean '条件該当回数記録
Dim SORTRANGE As Range 'ｿｰﾄ用
Dim SORTKEY1 As Range 'ｿｰﾄKEY用
Dim SORTKEY2 As Range 'ｿｰﾄKEY用

'ﾜｰｸｼｰﾄを格納できるWorksheet型
Dim WS1 As Worksheet 'Sheets("DATA")
Dim WS2 As Worksheet 'Sheets("修正")
Dim WS3 As Worksheet 'Sheets("年集計")
Dim WS4 As Worksheet 'Sheets("月集計")
Dim WS5 As Worksheet 'Sheets("設定")

'ﾜｰｸｼｰﾄｵﾌﾞｼﾞｪｸﾄの格納
Set WS1 = Sheets("DATA")
Set WS2 = Sheets("修正")
Set WS3 = Sheets("年集計")
Set WS4 = Sheets("月集計")
Set WS5 = Sheets("設定")

  Application.StatusBar = "ｿｰﾄ処理中"
  
  EndRow = WS1.Cells(Rows.Count, 1).End(xlUp).Row 'DATAｼｰﾄMax行数
  'ｿｰﾄ用範囲の設定
  Set SORTRANGE = Range(WS1.Cells(1, 1), WS1.Cells(EndRow, 37))
  Set SORTKEY1 = Range(WS1.Cells(1, 32), WS1.Cells(EndRow, 32)) '現場歴実績日時
  Set SORTKEY2 = Range(WS1.Cells(1, 22), WS1.Cells(EndRow, 22)) '実績倉入日時
  
    WS1.Sort.SortFields.Clear
    WS1.Sort.SortFields.Add Key:=SORTKEY1, SortOn:=xlSortOnValues, Order:=xlDescending, DataOption:=xlSortNormal
    WS1.Sort.SortFields.Add Key:=SORTKEY2, SortOn:=xlSortOnValues, Order:=xlAscending, DataOption:=xlSortTextAsNumbers
    With WS1.Sort
        .SetRange SORTRANGE
        .Header = xlYes
        .MatchCase = False
        .Orientation = xlTopToBottom
        .SortMethod = xlPinYin
    End With

  Application.StatusBar = "基本情報処理中"

    If WS2.AutoFilterMode Then 'ｵｰﾄﾌｨﾙﾀ設定有り
      If WS2.AutoFilter.FilterMode Then '"絞り込まれています"
      Else '"絞り込まれていません"
      End If
      Rows("1:1").AutoFilter 'ｵｰﾄﾌｨﾙﾀ設定解除
    Else 'ｵｰﾄﾌｨﾙﾀ設定無し
    End If

  Range(WS2.Cells(1, 2), WS2.Cells(Rows.Count, 30)).ClearContents
  
  '2次元配列に一括読み込み******************************************
  '一括読み込み時は、要素が1からｽﾀｰﾄする。
  DB1 = Range(WS1.Cells(1, 1), WS1.Cells(EndRow, 50)) 'WS1:DATAｼｰﾄ
  DB2 = Range(WS2.Cells(1, 1), WS2.Cells(EndRow, 30))  'WS2:修正ｼｰﾄ
  Con = Range(WS5.Cells(1, 1), WS5.Cells(34, 24)) 'WS5:設定ｼｰﾄ
  '*****************************************************************
  
  DB2(1, 1) = "訂正用"
  DB2(1, 2) = "自動"
  DB2(1, 3) = "集計用"
  DB2(1, 4) = "倉入れ年月日"
  DB2(1, 5) = "倉入れ年"
  DB2(1, 6) = "倉入れ月"
  DB2(1, 7) = "倉入れ日"
  DB2(1, 8) = "計算製品単重"
  DB2(1, 9) = "倉入れ枚数"
  DB2(1, 10) = "同時VC"
  
  For a = 2 To EndRow
    If Gflag = True Then
      '直の日付（現場歴）
      DB2(a, 4) = Format(Replace(DB1(a, 34), "-", "/"), "yyyy/mm/dd")
      DB2(a, 5) = Mid(DB1(a, 34), 1, 4)       '現場歴年月日時間を年のみに変更
      DB2(a, 6) = Mid(DB1(a, 34), 6, 2)       '現場歴年月日時間を月のみに変更
      DB2(a, 7) = Mid(DB1(a, 34), 9, 2)       '現場歴年月日時間を日のみに変更
      
      Dim CK As Long
      CK = Val(Format(DB1(a, 34), "h")) 'ﾃﾞｰﾀから時刻を1桁取り出し数値に変換
'      Debug.Print CK
      
      If CK >= 7 And CK < 15 Then
        DB2(a, 23) = 1 '直
      ElseIf CK >= 15 And CK < 23 Then
        DB2(a, 23) = 2 '直
      Else
        DB2(a, 23) = 3 '直
      End If
      
      WS3.Cells(2, 2) = "現場歴" 'WS3:年集計ｼｰﾄ
      WS4.Cells(2, 2) = "現場歴" 'WS4:月集計ｼｰﾄ
    Else
      '通常の日付（太陽暦）
      DB2(a, 4) = Format(Replace(DB1(a, 22), "-", "/"), "yyyy/mm/dd")
      DB2(a, 5) = Mid(DB1(a, 22), 1, 4)     '倉入れ年月日を年のみに変更
      DB2(a, 6) = Mid(DB1(a, 22), 6, 2)     '倉入れ年月日を月のみに変更
      DB2(a, 7) = Mid(DB1(a, 22), 9, 2)     '倉入れ年月日を日のみに変更
      WS3.Cells(2, 2) = "太陽歴" 'WS3:年集計ｼｰﾄ
      WS4.Cells(2, 2) = "太陽歴" 'WS4:月集計ｼｰﾄ
    End If
    DB2(a, 8) = Format(Val(DB1(a, 13)) * Val(DB1(a, 14)) * Val(DB1(a, 15)) * Val(DB1(a, 24)) / (10 ^ 6), "0.00")   '計算製品単重＝製品厚*製品幅*製品丈*比重/10^6
    If DB2(a, 8) <> 0 Then
      DB2(a, 9) = Format(Val(DB1(a, 23)) / Val(DB2(a, 8)), "0")     '倉入枚数＝倉入重量/製品単重
    End If
  Next a
  
  Select Case Con(3, 5)
  Case Is = "製造":  n(1) = 0
  Case Is = "ｵｰﾀﾞｰ": n(1) = 5
  End Select
  
  Select Case Con(3, 15)
  Case Is = "製造":  n(2) = 0
  Case Is = "ｵｰﾀﾞｰ": n(2) = 5
  End Select

  
  '修正シートへデータを転記
  For a = 1 To EndRow
    DB2(a, 11) = DB1(a, 1) 'ﾛｯﾄ№
    DB2(a, 12) = DB1(a, 3) '検査№
    DB2(a, 13) = DB1(a, 11 - n(1)) '製造材質orｵｰﾀﾞｰ材質
    DB2(a, 14) = DB1(a, 12 - n(1)) '製造調質orｵｰﾀﾞｰ調質
    DB2(a, 15) = DB1(a, 13 - n(1)) '製造板厚orｵｰﾀﾞｰ板厚
    DB2(a, 16) = DB1(a, 14 - n(1)) '製造板幅orｵｰﾀﾞｰ板幅
    DB2(a, 17) = DB1(a, 15 - n(1)) '製造板丈orｵｰﾀﾞｰ板丈
    DB2(a, 18) = DB1(a, 21) 'VC
    DB2(a, 19) = DB1(a, 22) '倉入れ完了_年月日
    DB2(a, 20) = DB1(a, 23) '実績倉入れ重量
    DB2(a, 21) = DB1(a, 27) '製品単重
    DB2(a, 22) = DB1(a, 28) '実績設備ｺｰｽ
  Next a

  '生地orVCで分ける｡該当チェックはVC設備優先｡
  For a = 2 To EndRow  '該当データチェック
    Application.StatusBar = "ｸﾞﾙｰﾌﾟ分け実行中 " & a & "行目処理中" & Format(a / EndRow, "0.0%") & " 完了"
    StartFlag = True 'ﾌﾗｸﾞ条件初期化
    flagV = False 'ﾌﾗｸﾞ条件初期化
    'a = 1631
    For b = 5 To 19
    'b = 8
      'NOT条件を検索　該当する場合は除外
      If Con(b, 14) = "*" Then  '使用ﾌﾗｸﾞ(NOT)
        If Con(b, 15) = "*" Or Con(b, 15) = Val(DB1(a, 12 - n(2))) Then  '製造材質orｵｰﾀﾞｰ材質(NOT)
          If Con(b, 16) = "*" Or Con(b, 16) <= Val(DB1(a, 13 - n(2))) Then  '製造板厚orｵｰﾀﾞｰ板厚(NOT)
            If Con(b, 17) = "*" Or Con(b, 17) >= Val(DB1(a, 13 - n(2))) Then
              If Con(b, 18) = "*" Or Con(b, 18) <= Val(DB1(a, 14 - n(2))) Then '製造板巾orｵｰﾀﾞｰ板幅(NOT)
                If Con(b, 19) = "*" Or Con(b, 19) >= Val(DB1(a, 14 - n(2))) Then
                  If Con(b, 20) = "*" Or Con(b, 20) <= Val(DB1(a, 15 - n(2))) Then '製造板丈orｵｰﾀﾞｰ板丈(NOT)
                    If Con(b, 21) = "*" Or Con(b, 21) < Val(DB1(a, 15 - n(2))) Then
                      If Con(b, 22) = "*" Or Con(b, 22) >= Val(DB1(a, 15 - n(2))) Then
                        If Con(b, 23) = "*" Or Con(b, 23) >= Val(DB2(a, 8)) Then '計算製品単重(NOT)･･･計算値の方が正確なため計算値を使用
                          '対象外の為ｽｷｯﾌﾟ
                          Exit For
                        End If
                      End If
                    End If
                  End If
                End If
              End If
            End If
          End If
        End If
      End If
      '該当条件の検索
      If DB1(a, 16) <> 30 Or Val(DB1(a, 15)) > 0 Then '形状がｺｲﾙでない（板）
        If (Con(b, 5) = "*" Or Con(b, 5) = DB1(a, 11 - n(1))) Then '製造材質orｵｰﾀﾞｰ材質
          If (Con(b, 6) = "*" Or Con(b, 6) <= Val(DB1(a, 13 - n(1)))) Then '製造板厚orｵｰﾀﾞｰ板厚
          If (Con(b, 7) = "*" Or Con(b, 7) >= Val(DB1(a, 13 - n(1)))) Then
            If (Con(b, 8) = "*" Or Con(b, 8) <= Val(DB1(a, 14 - n(1)))) Then '製造板巾orｵｰﾀﾞｰ板幅
            If (Con(b, 9) = "*" Or Con(b, 9) >= Val(DB1(a, 14 - n(1)))) Then
              If (Con(b, 10) = "*" Or Con(b, 10) <= Val(DB1(a, 15 - n(1)))) Then '製造板丈orｵｰﾀﾞｰ板丈
              If (Con(b, 11) = "*" Or Con(b, 11) < Val(DB1(a, 15 - n(1)))) Then
              If (Con(b, 12) = "*" Or Con(b, 12) >= Val(DB1(a, 15 - n(1)))) Then
                If (Con(b, 13) = "*" Or Con(b, 13) >= Val(DB2(a, 8))) Then  '計算製品単重･･･計算値の方が正確なため計算値を使用
                      
                  '生地******************************************************************
                  If StartFlag = True Then '初回のみ記録
                      DB2(a, 2) = b - 4     '5行目から bが5～19 （1から15まで）
                      StartFlag = False
                  End If
                  
                  'LS4による同時VC対象か、確認
                  If DB1(a, 28) Like "*LS4 VC*" Then
                    If DB1(a, 36) = "B535" Then 'VCが表のみで、かつ"B535"の時
                      If DB1(a, 37) = "" Then
                        flagV = True
                        DB2(a, 10) = "L"
                      End If
                    End If
                  ElseIf DB1(a, 28) Like "*FS4 VC*" Then
                    If DB1(a, 36) = "SPV2001" Or DB1(a, 36) = "AL-200" Then 'VCが表のみで、かつ"SPV2001"or"AL-200"の時
                      If DB1(a, 37) = "" Then
                        flagV = True
                        DB2(a, 10) = "F"
                      End If
                    End If
                  End If
                  
                  'VC********************************************************************
                  If flagV = False Then
                    If Con(b, 4) = "*" And (Val(DB1(a, 21)) = 1 Or DB1(a, 21) = "01") Then
                      'VC条件該当の場合は上書き
                      DB2(a, 2) = b - 4 '5行目から bが5～19 （1から15まで）
                      Exit For '該当したので条件検索から抜ける
                    End If
                  End If
                  
                End If
              End If
              End If
              End If
            End If
            End If
          End If
          End If
        End If
      End If
        
    Next b
  Next a
  
  '一括貼り付け
  Range(WS2.Cells(1, 1), WS2.Cells(EndRow, 30)) = DB2  'WS2:修正ｼｰﾄ

  '集計用計算式(修正ｼｰﾄ)
  WS2.Cells(2, 3) = "=IF(RC[-2]="""",RC[-1],RC[-2])"
  WS2.Cells(2, 3).Copy Destination:=Range(WS2.Cells(2, 3), WS2.Cells(EndRow, 3))

  '年集計(WS3:年集計ｼｰﾄ)
  MaxRow = WS3.Cells(Rows.Count, 2).End(xlUp).Row
  '重量
  WS3.Cells(5, 4) = "=IF(R4C="""","""",SUMIFS(修正!R2C20:R" & EndRow & "C20,修正!R2C3:R" & EndRow & "C3,年集計!R3C,修正!R2C4:R" & EndRow & "C4,年集計!RC2))"
  WS3.Cells(5, 4).Copy Destination:=Range(WS3.Cells(5, 4), WS3.Cells(MaxRow, 18))
  '枚数
  WS3.Cells(5, 20) = "=IF(R4C="""","""",SUMIFS(修正!R2C9:R" & EndRow & "C9,修正!R2C3:R" & EndRow & "C3,年集計!R3C,修正!R2C4:R" & EndRow & "C4,年集計!RC2))"
  WS3.Cells(5, 20).Copy Destination:=Range(WS3.Cells(5, 20), WS3.Cells(MaxRow, 34))
  '重量総計
  WS3.Cells(5, 3) = "=SUM(RC[1]:RC[15])"
  WS3.Cells(5, 3).Copy Destination:=Range(WS3.Cells(5, 3), WS3.Cells(MaxRow, 3))
  '枚数総計
  WS3.Cells(5, 19) = "=SUM(RC[1]:RC[15])"
  WS3.Cells(5, 19).Copy Destination:=Range(WS3.Cells(5, 19), WS3.Cells(MaxRow, 19))

  '月集計(WS4:月集計ｼｰﾄ)***************(集計月設定用)*********************************************
  WS4.Cells(2, 4) = Format(WS1.Cells(2, 32), "yyyy/mm")  'Format(DateAdd("m", 0, Date), "yyyy/mm")
  '***********************************************************************************************
  
  '重量
  WS4.Cells(4, 6) = "=IF(RC4="""","""",SUMIFS(修正!R2C20:R" & EndRow & "C20,修正!R2C3:R" & EndRow & "C3,月集計!RC3,修正!R2C5:R" & EndRow & "C5,YEAR(月集計!R2C4),修正!R2C6:R" & EndRow & "C6,MONTH(月集計!R2C4),修正!R2C7:R" & EndRow & "C7,月集計!R2C))"
  WS4.Cells(4, 6).Copy Destination:=Range(WS4.Cells(4, 6), WS4.Cells(18, 36))
  '枚数
  WS4.Cells(20, 6) = "=IF(RC4="""","""",SUMIFS(修正!R2C9:R" & EndRow & "C9,修正!R2C3:R" & EndRow & "C3,月集計!RC3,修正!R2C5:R" & EndRow & "C5,YEAR(月集計!R2C4),修正!R2C6:R" & EndRow & "C6,MONTH(月集計!R2C4),修正!R2C7:R" & EndRow & "C7,月集計!R2C))"
  WS4.Cells(20, 6).Copy Destination:=Range(WS4.Cells(20, 6), WS4.Cells(34, 36))
  'ｸﾞﾙｰﾌﾟ別集計
  WS4.Cells(4, 5).Copy Destination:=Range(WS4.Cells(4, 5), WS4.Cells(18, 5))
  WS4.Cells(4, 5).Copy Destination:=Range(WS4.Cells(20, 5), WS4.Cells(34, 5))

On Error Resume Next 'ｴﾗｰ回避
AppActivate "Microsoft Excel"
Err.Clear
Application.Calculation = xlCalculationAutomatic

Call クリック動作(False, TimeS) '集計し直し

Application.StatusBar = "集計完了しました。"  'ステータスバー
Set WSH = CreateObject("WScript.Shell")
WSH.Popup "集計完了しました。" & Chr(10) & Format(Timer - TimeS, "処理時間 0.0秒"), 1, "Title", vbInformation
Set WSH = Nothing

Application.StatusBar = False  'ステータスバー
Application.ScreenUpdating = True

End Sub


'+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
'よく使う記述
'EndRow = Cells(Rows.Count, 1).End(xlUp).Row　’最終行判断
'Application.ScreenUpdating = false　’画面更新停止
'Application.StatusBar = ""　’ステータスバー
'Application.Calculation = xlCalculationManual
'Application.Calculation = xlCalculationAutomatic
'Sub Auto_Open()
'UserForm.Show vbModeless
'FileName = "\\NLMFANAGO02D\仕掛ﾃﾞｰﾀ\sikalot.txt"
'filetime = FileDateTime(Filename) 'ファイル更新日の取得

