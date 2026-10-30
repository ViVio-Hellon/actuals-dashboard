"""SymNavi から RNE の結果を取り出す部分（RNEConverter から写したもの）。

出どころ: ViVio-Hellon/RNEConverter の app/ 以下（claude/quirky-keller-m5gvf2 の 8e41e3f 時点）
  extract/（symnavi.py・dde.py・naviapi.py・clipboard.py・winwatch.py・vbs/・ps/）
  transform/xls_reader.py・transform/tsv_reader.py・locking.py
は**中身を変えずに**写している（直す時は RNEConverter 側と同じ直しを入れる）。
config.py だけは、ここで使う 2 つの設定の型（SymNaviConfig・CscriptConfig）を写したもの。
"""
