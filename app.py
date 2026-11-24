"""ADO手元カンバン — Streamlit ラッパー

使い方:
    同じフォルダに index.html と fetch_ado.py を置いて
    streamlit run app.py

「ADOから取得」ボタンは fetch_ado.py を別プロセスで起動する。
（Streamlit のスレッド内で Playwright を直接動かすと、Windows で
  イベントループ関係のエラーになりやすいので分けている）
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

HERE = Path(__file__).parent
HTML = HERE / "index.html"
FETCH = HERE / "fetch_ado.py"
CFG = HERE / "ado_kanban_config.json"
OUT = HERE / "latest_ado.csv"

st.set_page_config(page_title="ADO手元カンバン", layout="wide")
st.markdown(
    """
    <style>
      .block-container {padding: 0.5rem 0.5rem 0 0.5rem; max-width: 100%;}
      header[data-testid="stHeader"] {height: 0; visibility: hidden;}
    </style>
    """,
    unsafe_allow_html=True,
)


def load_cfg():
    try:
        return json.loads(CFG.read_text(encoding="utf-8"))
    except Exception:
        return {}


cfg = load_cfg()
channels = ["msedge", "chrome", "chromium"]

with st.sidebar:
    st.subheader("ADOから自動取得")
    url = st.text_input("My Query のURL", cfg.get("url", ""),
                        help="My Queries からクエリを開いたときのアドレスバーのURL")
    channel = st.selectbox("使うブラウザ", channels, index=channels.index(cfg.get("channel", "msedge")))
    headed = st.checkbox("毎回ブラウザを表示する", cfg.get("headed", False))
    go = st.button("ADOから取得して取り込む", type="primary", disabled=not url.strip())
    st.caption("初回やログインが切れたときはブラウザが開くので、手でログインしてね。")
    st.divider()
    height = st.slider("表示の高さ(px)", 600, 2000, cfg.get("height", 950), 50)

new_cfg = {"url": url.strip(), "channel": channel, "headed": headed, "height": height}
if new_cfg != cfg:
    CFG.write_text(json.dumps(new_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

auto = None
if go:
    cmd = [sys.executable, str(FETCH), "--url", url.strip(), "--out", str(OUT), "--channel", channel]
    if headed:
        cmd.append("--headed")
    with st.spinner("ADOから取得中… ログイン画面が出たらブラウザでログインしてね"):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=480, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            log = (proc.stdout or "") + (proc.stderr or "")
            result = next((json.loads(l[7:]) for l in log.splitlines() if l.startswith("RESULT ")), None)
        except subprocess.TimeoutExpired:
            proc, log, result = None, "時間切れ（8分）", None
    if proc is not None and proc.returncode == 0 and result and OUT.exists():
        auto = {"id": int(time.time() * 1000), "text": OUT.read_text(encoding="utf-8-sig")}
        st.toast(f"ADOから {result['count']} 件取ってきたよ")
    else:
        st.error("取得に失敗しちゃった。ログを見てね")
        st.code(log[-3000:] or "(ログなし)")

html = HTML.read_text(encoding="utf-8")
if auto:
    # 取得したCSVを今回の描画にだけ埋め込む。ページ側が読み込んでカンバンに取り込む
    payload = json.dumps(auto, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("<!--AUTO-->", f"<script>window.__AUTO_CSV__={payload};</script>", 1)

components.html(html, height=height, scrolling=True)
