Attribute VB_Name = "【03】補助"
Option Explicit
Sub 現場歴集計()
Call グループ分け(True, Timer) '現場歴で集計
End Sub

Sub 太陽暦集計()
Call グループ分け(False, Timer) '太陽暦で集計
End Sub

Sub 説明フォーム起動()
UserForm01.Show vbModeless
End Sub

Sub チェックフォーム起動()
UserForm11.Show vbModeless
End Sub

Sub 集計月変更P()
Cells(2, 4) = Format(DateAdd("m", 1, Cells(2, 4)), "yyyy/mm")
End Sub
Sub 集計月変更M()
Cells(2, 4) = Format(DateAdd("m", -1, Cells(2, 4)), "yyyy/mm")
End Sub

Sub クリック動作(Flag As Boolean, TimeS As Double)

Application.ScreenUpdating = False

Dim DB(1 To 10000, 1 To 100) As Variant
Dim a As Long
Dim b As Long
Dim i As Long
Dim MaxB As Long
Dim EndRow As Long

'ﾜｰｸｼｰﾄを格納できるWorksheet型
Dim WS1 As Worksheet 'Sheets("DATA")
Dim WS2 As Worksheet 'Sheets("修正")
Dim WS3 As Worksheet 'Sheets("年集計")
Dim WS4 As Worksheet 'Sheets("月集計")
Dim WS5 As Worksheet 'Sheets("設定")
Dim WS6 As Worksheet 'Sheets("ｸﾞﾗﾌ")

'ﾜｰｸｼｰﾄｵﾌﾞｼﾞｪｸﾄの格納
Set WS1 = Sheets("DATA")
Set WS2 = Sheets("修正")
Set WS3 = Sheets("年集計")
Set WS4 = Sheets("月集計")
Set WS5 = Sheets("設定")
Set WS6 = Sheets("ｸﾞﾗﾌ")
 
    If Flag = True Then 'ｸﾘｯｸ判定
      Const RNG2 As String = "B3:B18" '削除範囲指定
      i = ActiveCell.Row
      WS6.Range(RNG2).ClearContents
      WS6.Cells(i, 2) = "レ"
    Else
      For i = 3 To 18
        If WS6.Cells(i, 2) = "レ" Then Exit For
      Next i
    End If
'    Debug.Print "i : " & i
    
    b = 1
    MaxB = 0
    EndRow = WS2.Cells(Rows.Count, 4).End(xlUp).Row
    Range(WS6.Cells(3, 23), WS6.Cells(Rows.Count, 47)).ClearContents
    
    Debug.Print "Flag : " & Flag & " " & "EndRow : " & EndRow

    For a = 2 To EndRow
      '選択した番号と条件選定番号が合っている情報のみ取得 Or ALL(18行目選択)
      'Debug.Print "Flag : " & Flag & " " & Val(WS2.Cells(a, 3)) & "=" & Val(i - 2) & "   i:" & i & "  =18"
      If Val(WS2.Cells(a, 3)) <> 0 Then '条件番号があるもの
        If Val(WS2.Cells(a, 3)) = Val(i - 2) Or i = 18 Then '選択したものまたは、ALLの場合
          If Val(WS1.Cells(a, 16)) = 10 Then  '形状が板(形状ｺｰﾄﾞ：10)のもののみ取得
            If Val(WS2.Cells(a, 6)) = Val(Right(WS4.Cells(2, 4), 2)) Then '集計対象月判定
              DB(b, 1) = WS2.Cells(a, 3) '条件番号
              
              DB(b, 2) = WS2.Cells(a, 6)   '月
              DB(b, 3) = WS2.Cells(a, 7)   '日
              DB(b, 4) = WS2.Cells(a, 23)   '直（計算）
              
              DB(b, 5) = WS1.Cells(a, 1) 'ロット番号
              DB(b, 6) = WS1.Cells(a, 3) '検査番号
              DB(b, 7) = WS1.Cells(a, 4) 'オーダー番号
              DB(b, 8) = WS1.Cells(a, 5) '用途コード
              
              DB(b, 9) = WS1.Cells(a, 11) '製造材質
              DB(b, 10) = WS1.Cells(a, 12) '製造調質
              DB(b, 11) = Format(WS1.Cells(a, 13), "0.000") '製造板厚
              DB(b, 12) = Format(WS1.Cells(a, 14), "0.00") '製造板幅
              DB(b, 13) = Format(WS1.Cells(a, 15), "0.00") '製造板丈
              
              DB(b, 14) = Format(WS2.Cells(a, 8), "0.00")  '製品単重
              
              DB(b, 15) = WS1.Cells(a, 6)  'オーダー材質
              DB(b, 16) = WS1.Cells(a, 7) 'オーダー調質
              DB(b, 17) = Format(WS1.Cells(a, 8), "0.000") 'オーダー板厚
              DB(b, 18) = Format(WS1.Cells(a, 9), "0.00") 'オーダー板幅
              DB(b, 19) = Format(WS1.Cells(a, 10), "0.00") 'オーダー板丈
              DB(b, 20) = WS1.Cells(a, 21) 'VC
              DB(b, 21) = WS1.Cells(a, 36) '表VC
              DB(b, 22) = WS1.Cells(a, 37) '裏VC
              DB(b, 23) = WS2.Cells(a, 10) '同時VC
              DB(b, 24) = WS2.Cells(a, 20) '重量
              DB(b, 25) = WS2.Cells(a, 9)  '枚数（計算）
    
              b = b + 1
              If MaxB < b Then MaxB = b
            End If
          End If
        End If
      End If
    Next a
  

  
  If MaxB >= 2 Then Range(WS6.Cells(3, 23), WS6.Cells(MaxB + 2, 47)) = DB

Application.ScreenUpdating = True
End Sub
