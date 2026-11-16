import logging

# 試験中のログは画面に出さない（失敗の調査は各試験の assert の文言で行う）
logging.getLogger().addHandler(logging.NullHandler())
logging.getLogger().setLevel(logging.CRITICAL)

# 試験では、設定・ログインの置き場所（ツールのフォルダの config）を各試験のデータの場所へ向ける
# （リポジトリの config/settings.json を書き換えない。子プロセスにも引き継がれる）
import os  # noqa: E402

os.environ["ACTUALS_TOOL_CONFIG"] = "{DATA}/config"
