Attribute VB_Name = "UserForm02"
Attribute VB_Base = "0{AFC7E12E-54E4-4075-BE52-BF23E27459BD}{42DDD871-BA7D-4F1B-9EAF-CDF7A83800A0}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = False

Private Sub CommandButton2_Click()
ThisWorkbook.Sheets("DATA").Cells.Delete Shift:=xlUp
MsgBox "DATAｼｰﾄのﾃﾞｰﾀを削除しました。"
End Sub

Private Sub CommandButton3_Click()
Unload Me
UserForm03.Show
End Sub
