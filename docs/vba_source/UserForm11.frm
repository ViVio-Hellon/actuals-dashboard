Attribute VB_Name = "UserForm11"
Attribute VB_Base = "0{F77ED00B-FC3E-40E4-BD9D-E3A59ECA617A}{5C198715-DC42-465D-AFBF-70BC1C7F426B}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = False

Private Sub CB1_Change()
For i = 23 To 38
  UserForm11.Controls("TB" & i) = Sheets("ｸﾞﾗﾌ").Cells(UserForm11.CB1.ListIndex + 3, i + 7)
Next i
End Sub

Private Sub CommandButton1_Click()

If UserForm11.CB1 = "" Then
 MsgBox "ﾛｯﾄが選択されていません。選択し、再度実行してください"
 Exit Sub
End If

Dim EndRow As Long '最終行判定
Dim MaxRow As Long '最終行判定
Dim flagV As Boolean '同時VCﾌﾗｸﾞ
Dim i As Long  '汎用ｶｳﾝﾀ
Dim n(1 To 2) As Long '製造orｵｰﾀﾞｰ判断用
Dim Con As Variant '条件用2次元配列
Dim zai(1 To 2) As Variant    '製造材質
Dim tyo(1 To 2) As Variant  '製造調質
Dim atu(1 To 2) As Variant  '製造板厚
Dim hab(1 To 2) As Variant  '製造板幅
Dim tak(1 To 2) As Variant  '製造板丈
Dim Weight As Variant  '製品単重
Dim VC As Variant  'VC
  
Dim StartFlag As Boolean '条件該当回数記録

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
  
  EndRow = WS1.Cells(Rows.Count, 1).End(xlUp).Row 'DATAｼｰﾄMax行数
  
  '一括読み込み
  Con = Range(WS5.Cells(1, 1), WS5.Cells(34, 24)) 'WS5:設定ｼｰﾄ
  '検索用条件の読み込み

  Select Case Con(3, 15)
  Case Is = "製造"
    zai(1) = UserForm11.TB24 '製造材質
    tyo(1) = UserForm11.TB25 '製造調質
    atu(1) = UserForm11.TB26 '製造板厚
    hab(1) = UserForm11.TB27 '製造板幅
    tak(1) = UserForm11.TB28 '製造板丈
  Case Is = "ｵｰﾀﾞｰ"
    zai(1) = UserForm11.TB30 'ｵｰﾀﾞｰ材質
    tyo(1) = UserForm11.TB31 'ｵｰﾀﾞｰ調質
    atu(1) = UserForm11.TB32 'ｵｰﾀﾞｰ板厚
    hab(1) = UserForm11.TB33 'ｵｰﾀﾞｰ板幅
    tak(1) = UserForm11.TB34 'ｵｰﾀﾞｰ板丈
  End Select
        
  Select Case Con(3, 5)
  Case Is = "製造"
    zai(2) = UserForm11.TB24 '製造材質
    tyo(2) = UserForm11.TB25 '製造調質
    atu(2) = UserForm11.TB26 '製造板厚
    hab(2) = UserForm11.TB27 '製造板幅
    tak(2) = UserForm11.TB28 '製造板丈
  Case Is = "ｵｰﾀﾞｰ"
    zai(2) = UserForm11.TB30 'ｵｰﾀﾞｰ材質
    tyo(2) = UserForm11.TB31 'ｵｰﾀﾞｰ調質
    atu(2) = UserForm11.TB32 'ｵｰﾀﾞｰ板厚
    hab(2) = UserForm11.TB33 'ｵｰﾀﾞｰ板幅
    tak(2) = UserForm11.TB34 'ｵｰﾀﾞｰ板丈
  End Select
  
  Weight = UserForm11.TB29 '製品単重
  VC = UserForm11.TB35 'VC

  '生地orVCで分ける｡該当チェックはVC設備優先｡
  For i = 1 To 15
  UserForm11.Controls("A" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("B" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("C" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("D" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("E" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("F" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("G" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("H" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("I" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("J" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("K" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("L" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("M" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("N" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("O" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("P" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("Q" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("R" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("S" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("T" & i).BackColor = RGB(250, 250, 250)
  UserForm11.Controls("Gr" & i).BackColor = RGB(250, 250, 250)
  Next i

    StartFlag = True 'ﾌﾗｸﾞ条件初期化
    flagV = False 'ﾌﾗｸﾞ条件初期化
    
    For i = 1 To 15
    
    
NOT条件HIT:
      'NOT条件を検索　該当する場合は除外
      If Con(i + 4, 14) = "*" Then '使用ﾌﾗｸﾞ(NOT)
        UserForm11.Controls("K" & i).BackColor = RGB(200, 200, 250)
        If Con(i + 4, 15) = "*" Or Con(i + 4, 15) = zai(1) Then '製造材質orｵｰﾀﾞｰ材質(NOT)
          UserForm11.Controls("L" & i).BackColor = RGB(200, 200, 250)
          If Con(i + 4, 16) = "*" Or Con(i + 4, 16) <= Val(atu(1)) Then '製造板厚orｵｰﾀﾞｰ板厚(NOT)
            UserForm11.Controls("M" & i).BackColor = RGB(200, 200, 250)
            If Con(i + 4, 17) = "*" Or Con(i + 4, 17) >= Val(atu(1)) Then
              UserForm11.Controls("N" & i).BackColor = RGB(200, 200, 250)
              If Con(i + 4, 18) = "*" Or Con(i + 4, 18) <= Val(hab(1)) Then '製造板巾orｵｰﾀﾞｰ板幅(NOT)
                UserForm11.Controls("O" & i).BackColor = RGB(200, 200, 250)
                If Con(i + 4, 19) = "*" Or Con(i + 4, 19) >= Val(hab(1)) Then
                  UserForm11.Controls("P" & i).BackColor = RGB(200, 200, 250)
                  If Con(i + 4, 20) = "*" Or Con(i + 4, 20) <= Val(tak(1)) Then '製造板丈orｵｰﾀﾞｰ板丈(NOT)
                    UserForm11.Controls("Q" & i).BackColor = RGB(200, 200, 250)
                    If Con(i + 4, 21) = "*" Or Con(i + 4, 21) < Val(tak(1)) Then
                      UserForm11.Controls("R" & i).BackColor = RGB(200, 200, 250)
                      If Con(i + 4, 22) = "*" Or Con(i + 4, 22) >= Val(tak(1)) Then
                        UserForm11.Controls("S" & i).BackColor = RGB(200, 200, 250)
                        If Con(i + 4, 23) = "*" Or Con(i + 4, 23) >= Val(Weight) Then '製品単重(NOT)
                          UserForm11.Controls("T" & i).BackColor = RGB(200, 200, 250)
                          '対象外の為ｽｷｯﾌﾟ
                          i = i + 1
                          GoTo NOT条件HIT
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
        If (Con(i + 4, 5) = "*" Or Con(i + 4, 5) = zai(2)) Then '製造材質orｵｰﾀﾞｰ材質
          UserForm11.Controls("B" & i).BackColor = RGB(250, 200, 200)
          If StartFlag = True Then UserForm11.Controls("B" & i).BackColor = RGB(200, 200, 250)
          If (Con(i + 4, 6) = "*" Or Con(i + 4, 6) <= Val(atu(2))) Then '製造板厚orｵｰﾀﾞｰ板厚
            UserForm11.Controls("C" & i).BackColor = RGB(250, 200, 200)
            If StartFlag = True Then UserForm11.Controls("C" & i).BackColor = RGB(200, 200, 250)
          If (Con(i + 4, 7) = "*" Or Con(i + 4, 7) >= Val(atu(2))) Then
            UserForm11.Controls("D" & i).BackColor = RGB(250, 200, 200)
            If StartFlag = True Then UserForm11.Controls("D" & i).BackColor = RGB(200, 200, 250)
            If (Con(i + 4, 8) = "*" Or Con(i + 4, 8) <= Val(hab(2))) Then '製造板巾orｵｰﾀﾞｰ板幅
              UserForm11.Controls("E" & i).BackColor = RGB(250, 200, 200)
              If StartFlag = True Then UserForm11.Controls("E" & i).BackColor = RGB(200, 200, 250)
            If (Con(i + 4, 9) = "*" Or Con(i + 4, 9) >= Val(hab(2))) Then
              UserForm11.Controls("F" & i).BackColor = RGB(250, 200, 200)
              If StartFlag = True Then UserForm11.Controls("F" & i).BackColor = RGB(200, 200, 250)
              If (Con(i + 4, 10) = "*" Or Con(i + 4, 10) <= Val(tak(2))) Then '製造板丈orｵｰﾀﾞｰ板丈
              UserForm11.Controls("G" & i).BackColor = RGB(250, 200, 200)
              If StartFlag = True Then UserForm11.Controls("G" & i).BackColor = RGB(200, 200, 250)
              If (Con(i + 4, 11) = "*" Or Con(i + 4, 11) < Val(tak(2))) Then
              UserForm11.Controls("H" & i).BackColor = RGB(250, 200, 200)
              If StartFlag = True Then UserForm11.Controls("H" & i).BackColor = RGB(200, 200, 250)
              If (Con(i + 4, 12) = "*" Or Con(i + 4, 12) >= Val(tak(2))) Then
              UserForm11.Controls("I" & i).BackColor = RGB(250, 200, 200)
              If StartFlag = True Then UserForm11.Controls("I" & i).BackColor = RGB(200, 200, 250)
                If (Con(i + 4, 13) = "*" Or Con(i + 4, 13) >= Val(Weight)) Then '製品単重
                
                  '生地******************************************************************
                  If Con(i + 4, 4) = "" Then
                  UserForm11.Controls("J" & i).BackColor = RGB(250, 200, 200)
                    If StartFlag = True Then
                      UserForm11.Controls("J" & i).BackColor = RGB(200, 200, 250) '初回のみ記録
                      UserForm11.Controls("Gr" & i).BackColor = RGB(200, 200, 250) '初回のみ記録
                      StartFlag = False
                    End If
                  End If
                  
                  '同時VC対象か、確認
                  If UserForm11.TB38 = "L" Or UserForm11.TB38 = "F" Then
                    flagV = True
                  End If

                  'VC********************************************************************
                  If flagV = False Then
                  
                    'VC条件該当の場合は上書き
                    If Con(i + 4, 4) = "*" And (VC = 1 Or VC = "01") Then
                    
                      '生地該当箇所の上書き
                      For a = 1 To 15
                        UserForm11.Controls("Gr" & a).BackColor = RGB(250, 250, 250) '初回のみ記録
                      Next a
                    
                    UserForm11.Controls("A" & i).BackColor = RGB(200, 200, 250) '初回のみ記録
                    UserForm11.Controls("J" & i).BackColor = RGB(250, 200, 200) '初回のみ記録
                    UserForm11.Controls("Gr" & i).BackColor = RGB(200, 200, 250) '初回のみ記録

                      GoTo 条件HIT
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
        
    Next i
    
'ﾜｰｸｼｰﾄｵﾌﾞｼﾞｪｸﾄの解放
Set WS1 = Nothing
Set WS2 = Nothing
Set WS3 = Nothing
Set WS4 = Nothing
Set WS5 = Nothing
    
条件HIT:
    
End Sub

Private Sub CommandButton2_Click()

Dim i As Long
'ﾜｰｸｼｰﾄを格納できるWorksheet型
Dim WS5 As Worksheet 'Sheets("設定")

'ﾜｰｸｼｰﾄｵﾌﾞｼﾞｪｸﾄの格納
Set WS5 = Sheets("設定")

For i = 1 To 15
  With UserForm11
    .Controls("Gr" & i) = WS5.Cells(i + 4, 3)
    .Controls("A" & i) = WS5.Cells(i + 4, 4)
    .Controls("B" & i) = WS5.Cells(i + 4, 5)
    .Controls("C" & i) = WS5.Cells(i + 4, 6)
    .Controls("D" & i) = WS5.Cells(i + 4, 7)
    .Controls("E" & i) = WS5.Cells(i + 4, 8)
    .Controls("F" & i) = WS5.Cells(i + 4, 9)
    .Controls("G" & i) = WS5.Cells(i + 4, 10)
    .Controls("H" & i) = WS5.Cells(i + 4, 11)
    .Controls("I" & i) = WS5.Cells(i + 4, 12)
    .Controls("J" & i) = WS5.Cells(i + 4, 13)
    .Controls("K" & i) = WS5.Cells(i + 4, 14)
    .Controls("L" & i) = WS5.Cells(i + 4, 15)
    .Controls("M" & i) = WS5.Cells(i + 4, 16)
    .Controls("N" & i) = WS5.Cells(i + 4, 17)
    .Controls("O" & i) = WS5.Cells(i + 4, 18)
    .Controls("P" & i) = WS5.Cells(i + 4, 19)
    .Controls("Q" & i) = WS5.Cells(i + 4, 20)
    .Controls("R" & i) = WS5.Cells(i + 4, 21)
    .Controls("S" & i) = WS5.Cells(i + 4, 22)
    .Controls("T" & i) = WS5.Cells(i + 4, 23)
  End With
Next i

'ﾜｰｸｼｰﾄｵﾌﾞｼﾞｪｸﾄの解放
Set WS5 = Nothing

End Sub

Private Sub UserForm_Initialize()
Dim i As Long
UserForm11.CB1.Clear
EndRow = Sheets("ｸﾞﾗﾌ").Cells(Rows.Count, 27).End(xlUp).Row   'Max行数
If EndRow <= 2 Then
  MsgBox "対象ﾃﾞｰﾀがありません。ﾁｪｯｸﾎﾞｯｸｽを選択しなおして再度実行してください"
  Exit Sub
End If
For i = 3 To EndRow
  UserForm11.CB1.AddItem Cells(i, 27) 'ﾛｯﾄ№ﾃﾞｰﾀ取り込み
Next i

'ﾛｯﾄﾃﾞｰﾀはﾛｯﾄ№が選択されたことによって　CB1_Change　で取り込まれる
If cnt >= 0 Then UserForm11.CB1.ListIndex = 0

'ﾜｰｸｼｰﾄを格納できるWorksheet型
Dim WS5 As Worksheet 'Sheets("設定")

'ﾜｰｸｼｰﾄｵﾌﾞｼﾞｪｸﾄの格納
Set WS5 = Sheets("設定")

For i = 1 To 15
  With UserForm11
    .Controls("Gr" & i) = WS5.Cells(i + 4, 3)
    .Controls("A" & i) = WS5.Cells(i + 4, 4)
    .Controls("B" & i) = WS5.Cells(i + 4, 5)
    .Controls("C" & i) = WS5.Cells(i + 4, 6)
    .Controls("D" & i) = WS5.Cells(i + 4, 7)
    .Controls("E" & i) = WS5.Cells(i + 4, 8)
    .Controls("F" & i) = WS5.Cells(i + 4, 9)
    .Controls("G" & i) = WS5.Cells(i + 4, 10)
    .Controls("H" & i) = WS5.Cells(i + 4, 11)
    .Controls("I" & i) = WS5.Cells(i + 4, 12)
    .Controls("J" & i) = WS5.Cells(i + 4, 13)
    .Controls("K" & i) = WS5.Cells(i + 4, 14)
    .Controls("L" & i) = WS5.Cells(i + 4, 15)
    .Controls("M" & i) = WS5.Cells(i + 4, 16)
    .Controls("N" & i) = WS5.Cells(i + 4, 17)
    .Controls("O" & i) = WS5.Cells(i + 4, 18)
    .Controls("P" & i) = WS5.Cells(i + 4, 19)
    .Controls("Q" & i) = WS5.Cells(i + 4, 20)
    .Controls("R" & i) = WS5.Cells(i + 4, 21)
    .Controls("S" & i) = WS5.Cells(i + 4, 22)
    .Controls("T" & i) = WS5.Cells(i + 4, 23)
  End With
Next i

'ﾜｰｸｼｰﾄｵﾌﾞｼﾞｪｸﾄの解放
Set WS5 = Nothing

End Sub
