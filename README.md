# Second Brain MCP Server

知識層（Second Brain）とプロジェクト層を接続する第3層のインターフェース。

## セットアップ

### 1. 依存インストール（WSL内）

```bash
cd 90_System/mcp-server
pip install -e .
# or
pip install mcp[cli] pyyaml
```

### 2. Claude Code に MCP サーバーを登録

プロジェクトの `.mcp.json` に追加:

```json
{
  "mcpServers": {
    "second-brain": {
      "command": "wsl.exe",
      "args": ["bash", "-c", "cd /mnt/c/Users/YoNishioka/Documents/second-brain/second-brain/90_System/mcp-server && python3 server.py"],
      "env": {
        "SECOND_BRAIN_PATH": "/mnt/c/Users/YoNishioka/Documents/second-brain/second-brain",
        "SECOND_BRAIN_GIT_SYNC": "false"
      }
    }
  }
}
```

別のPCで使う場合は `SECOND_BRAIN_PATH` をそのPCのcloneパスに変更する。

リモートリポジトリとの同期を有効にするには `SECOND_BRAIN_GIT_SYNC=true` を設定。

## 提供ツール

### `query_knowledge`
Second Brain から関連知識を検索する。

- `context` (string): 現在の文脈・課題
- `domains` (string[], optional): フィルタ用ドメインタグ
- `depth` ("index"|"summary"|"full"): 返却の詳細度

### `get_recent`
指定日以降の新規・更新ノートを取得する。

- `since` (string): ISO日付
- `domains` (string[], optional): フィルタ用ドメインタグ

### `capture_insight`
プロジェクトの気づきを Second Brain に還元する。

- `title` (string): タイトル
- `content` (string): 本文
- `abstract_principles` (string[]): 抽象原則
- `applicable_domains` (string[]): 応用領域タグ
- `source_project` (string, optional): 元プロジェクト名

## 設計背景

詳細は [[sense-model-context-engineering]] を参照。
