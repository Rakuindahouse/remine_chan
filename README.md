# りまいんちゃん 🔔

Discordサーバーでのプロジェクト管理を助ける、日本語対応のリマインドBotです。

---

## 特徴

- 💬 **自然言語検知** — 「今日やる」「明日PRを出す」などの雑なチャットを自動で検知してリマインドを設定
- ⏰ **コマンド設定** — `/remind` コマンドで日時を指定してリマインドを設定
- 📢 **専用チャンネル通知** — リマインドは指定チャンネルに `@everyone` 付きで送信
- 🔗 **元メッセージリンク付き** — 何の話だったか一目でわかる
- 🗑 **ドロップダウン削除** — IDを調べなくてもリストから選んでキャンセルできる

---

## 仕組み

```
Discordのチャット
      ↓
「今日やる」などのキーワードを検知
      ↓
日付（今日・明日・今週中など）と動作動詞（やる・する・出すなど）を確認
      ↓
Supabase（PostgreSQL）にリマインドを保存
      ↓
1分ごとに期限チェック → 時間になったらリマインドチャンネルに通知
```

**スリープ対策：**  
Render無料枠のスリープを防ぐため、Bot内蔵のWebサーバー（`/` エンドポイント）にUptimeRobotが5分ごとにアクセスして起こし続けています。

---

## コマンド一覧

| コマンド | 説明 |
|---|---|
| `/remind [テキスト]` | 自由テキストでリマインドを設定（例: `明日PRを出す`） |
| `/reminders` | 設定中のリマインド一覧を表示 |
| `/cancel` | ドロップダウンからリマインドを選んでキャンセル |
| `/testremind` | テスト通知を送信（動作確認用） |
| `/setreminderchannel` | 通知先チャンネルを設定（管理者のみ） |
| `/setdefaulttime` | デフォルトのリマインド時刻を変更（管理者のみ、デフォルト: 23:30） |

**自動検知の例：**
```
今日やる        → 今日 23:30
明日PRを出す    → 明日 23:30
今週中にデプロイする → 今週末 23:30
来週ミーティング準備 14:00 → 来週末 14:00
```

---

## 技術スタック

| 役割 | 技術 |
|---|---|
| Bot本体 | Python / discord.py |
| データベース | PostgreSQL（Supabase） |
| ホスティング | Render（Web Service） |
| スリープ対策 | UptimeRobot |

---

## セットアップ

### 必要なもの

- Python 3.9以上
- Discordのbot token（[Discord Developer Portal](https://discord.com/developers/applications)）
- Supabaseのアカウント（[supabase.com](https://supabase.com)）

### ローカルで動かす

```bash
git clone https://github.com/Rakuindahouse/remine_chan.git
cd remine_chan
pip install -r requirements.txt
cp .env.example .env
# .env を編集して DISCORD_TOKEN と DATABASE_URL を設定
python bot.py
```

### 環境変数

| 変数名 | 説明 |
|---|---|
| `DISCORD_TOKEN` | Discord Developer Portalで取得したトークン |
| `DATABASE_URL` | SupabaseのPostgreSQL接続URL |
| `TZ` | タイムゾーン（例: `Asia/Tokyo`） |

### Discordの権限設定

Bot Intentで **MESSAGE CONTENT INTENT** を有効化してください。  
必要な権限: `Send Messages` / `Add Reactions` / `Embed Links` / `Mention Everyone`

### Renderへのデプロイ

1. このリポジトリをGitHubにpush
2. [Render](https://render.com) でWeb Serviceを作成しリポジトリを連携
3. 環境変数（`DISCORD_TOKEN` / `DATABASE_URL` / `TZ`）を設定
4. [UptimeRobot](https://uptimerobot.com) でRenderのURLを5分間隔で監視設定

---

## ファイル構成

```
.
├── bot.py          # Bot本体・スラッシュコマンド・Webサーバー
├── detector.py     # 自然言語検知・日時パーサー
├── storage.py      # データベース操作（PostgreSQL）
├── requirements.txt
└── .env.example
```
