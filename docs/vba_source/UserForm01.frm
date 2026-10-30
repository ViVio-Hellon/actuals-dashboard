Attribute VB_Name = "UserForm01"
Attribute VB_Base = "0{2AF63D74-ABA4-4802-B0E4-A47F5AC5E0E9}{1911B84A-2BAB-454E-9F57-DCBF94996A94}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = False

Private Sub UserForm_Initialize()
Dim buf As String
Dim cnt As Long
Dim objNetWork As Object

Set objNetWork = CreateObject("WScript.Network") 'ネットワークオブジェクトの作成

With Me
  '通常設定*******************************************
'  .TB3 = LCase(objNetWork.UserName) '小文字にする'専用設定使用時はコメントブロック対象
'  n = InStr(.TB3, "-") '専用設定使用時はコメントブロック対象
'  .TB4 = Mid(.TB3, n + 1, Len(.TB3) - (n)) '専用設定使用時はコメントブロック対象
  '***************************************************
  
  '専用設定*******************************************
  ''使用時は通常設定をｺﾒﾝﾄｱｳﾄして、専用設定を使用
  ''XとYを使用する値に書き換える
  '.TB3 = "XXXXXX-XXXXXX" 'IDを固定値にする場合に使用
  '.TB4 = "YYYYYY" 'PASSを固定値にする場合に使用
  .TB3 = "＜伏せ字＞" '梱包課専用
  .TB4 = "＜伏せ字＞" '梱包課専用
  '***************************************************
  
  cnt = 0
  .TB2 = ThisWorkbook.Path
  buf = Dir(ThisWorkbook.Path & "\" & "*.RNE")
  .CB1.Clear
  Do While buf <> ""
      cnt = cnt + 1
      .CB1.AddItem buf
      buf = Dir()
  Loop
  If cnt > 0 Then .CB1.ListIndex = 0

End With

End Sub


Private Sub CommandButton1_Click()
If Me.TB3 <> "" And Me.TB4 <> "" Then
  If Me.CB1 = "" Then
    MsgBox "データが存在しません" & vbCrLf & "処理を中止しました", vbCritical
    Unload Me
  Else
    Unload Me
    DoEvents 'ユーザーフォーム消去促進
    'データ作成引数説明 : RNEName, RNEPath, ID, PASS
    Dim TimeS As Double '時間計測用
    TimeS = Timer
    Call データ作成(Me.CB1, ThisWorkbook.Path & "\" & Me.CB1, Me.TB3, Me.TB4, Timer)
  End If
Else
  MsgBox "IDとPASSを入力してください。"
  Unload Me
End If
End Sub

Private Sub CommandButton2_Click()
Unload Me
Application.StatusBar = ""
UserForm02.Show
End Sub

Private Sub CommandButton3_Click()
  Dim dlg As FileDialog
  Set dlg = Application.FileDialog(msoFileDialogFolderPicker)
  If dlg.Show = False Then Exit Sub 'キャンセルボタンクリック時にマクロを終了
  Me.OB2 = True
  Me.TB2 = dlg.SelectedItems(1)
End Sub
Private Sub OB1_Click()
Me.TB2 = ThisWorkbook.Path
Dim buf As String
Dim cnt As Long
buf = Dir(UserForm01.TB2 & "\" & "*.RNE")
Me.CB1.Clear
Do While buf <> ""
    cnt = cnt + 1
    Me.CB1.AddItem buf
    buf = Dir()
Loop
If cnt > 0 Then UserForm01.CB1.ListIndex = 0
End Sub
Private Sub OB2_Click()
Me.TB2 = ""
Dim buf As String
Dim cnt As Long
buf = Dir(Me.TB2 & "\" & "*.RNE")
UserForMem01.CB1.Clear
Do While buf <> ""
    cnt = cnt + 1
    Me.CB1.AddItem buf
    buf = Dir()
Loop
If cnt > 0 Then Me.CB1.ListIndex = 0
End Sub



