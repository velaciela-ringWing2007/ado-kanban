# ado-kanban — ADO手元カンバン

Azure DevOps の My Query で拾ったタスクを手元のカンバンで管理するツール。
ADO 側の状態は毎日取り込み、自分用の並び順・中断レーン・親子・リンゴ優先度・メモは手元に残す。

## ファイル

| ファイル | 役割 |
|---|---|
| `index.html` | カンバン本体（単体でブラウザで開いても動く） |
| `app.py` | Streamlit ラッパー。サイドバーに「ADOから取得」ボタン |
| `fetch_ado.py` | Playwright で ADO からクエリ結果を取得して CSV 化（単体 CLI でも使える） |
| `requirements.txt` | 依存パッケージ |

実行時にできるファイル（git 管理外）: `ado_kanban_config.json`（クエリURLなど）, `latest_ado.csv`（最後に取った CSV）

## セットアップ

```
pip install -r requirements.txt
streamlit run app.py
```

- ブラウザは既定で PC の Edge（`channel=msedge`）を使うので `playwright install` は不要。
  Chromium を使うときだけ `playwright install chromium`。
- サイドバーに My Query を開いたときの URL を貼って「ADOから取得して取り込む」。

## 取得のしくみ（fetch_ado.py）

1. 専用プロファイル `~/.ado-kanban/browser-profile` でブラウザを起動（ログイン状態はここに残る）
2. まず画面なしで API を叩く → ログイン済みならそのまま取得
3. 未ログインなら画面付きでクエリを開き、**手動ログイン**を最大5分待つ
4. ログイン済みセッションで REST API を呼ぶ
   - `GET {org}/{project}/_apis/wit/wiql/{queryId}` … ID 一覧（ツリー型なら親子も）
   - `GET {org}/{project}/_apis/wit/workitems?ids=…&fields=…` … 200件ずつ詳細
5. `ID, Work Item Type, Title, Assigned To, State, Parent, Tags, Iteration Path` の CSV を出力

画面の「CSVにエクスポート」ボタンは使っていない（UI変更・日本語UIに弱いため）。
Playwright は Streamlit 内ではなく別プロセスで起動（Windows のイベントループ問題を避けるため）。

単体実行:
```
python fetch_ado.py --url "https://dev.azure.com/組織/プロジェクト/_queries/query/<GUID>/" --out latest_ado.csv
```
オプション: `--channel msedge|chrome|chromium`, `--headed`（毎回画面表示）, `--login-timeout 300`, `--api-version 7.0`

## カンバンの取り込みルール

- ADO で状態が変わっていないカードは動かさない（自分で「中断」に置いた Doing はそのまま）
- Done/Closed 系になったら、どこにあっても Done へ自動移動
- その他の状態変化は、カードが「元の状態に対応するレーン」にいるときだけ移動。
  自分で別レーンに置いていたら動かさず「ADO変更」バッジだけ
- 自動移動したカードは赤く光る（クリックで消える）
- CSV から消えたカードは削除せず「CSVに無い」バッジ
- 状態→レーンの対応は設定画面で上書き可能。見出しが自動判定できない CSV は列の割り当て画面が出る

## 操作

- レーン内の並び順＝優先度。「リンゴが多い順」でリンゴ数ソート（子は親の下で並ぶ）
- カードの真ん中にドロップ → 子にする / 上下端 → 並び替え
- リンゴはカード上の 5 個をクリック、または編集画面の −/＋（最大99）
- 元に戻す: Ctrl+Z / ボタン
- データはブラウザの localStorage。設定画面から JSON バックアップ

## 未検証・注意（2026-11-25 時点）

- **本物の ADO では未テスト。** URL分解・API応答→CSV変換・自動取り込みはモックでテスト済み。
  「ブラウザのログイン Cookie だけで REST API が通る」は仮説。だめなら UI のエクスポートボタン方式に切り替える
- Streamlit の埋め込み iframe 内で localStorage が再読み込み後も残るかは未確認
- 条件付きアクセス等の設定次第で、ログイン状態が保存されず毎回ログインになる可能性
- `~/.ado-kanban/browser-profile` にはログイン Cookie が入るので共有しない

## 次にやるかも

- データ保存を localStorage からサーバ側 JSON へ（Streamlit カスタムコンポーネント or FastAPI）
- 取得失敗時のフォールバック（エクスポートボタン方式）
