import logging

# 試験中のログは画面に出さない（失敗の調査は各試験の assert の文言で行う）
logging.getLogger().addHandler(logging.NullHandler())
logging.getLogger().setLevel(logging.CRITICAL)
