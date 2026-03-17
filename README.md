# Second Brain MCP Server

知識層（Second Brain）とプロジェクト層を接続する第3層のインターフェース。

## セットアップ

### Claude Code に MCP サーバーを登録

プロジェクトの `.claude/settings.json` または `~/.claude/settings.json` に追加:

```json
{
  "mcpServers": {
    "second-brain": {
      "command": "uvx",
      "args": ["second-brain-mcp", "--vault", "~/Documents/second-brain/second-brain"]
    }
  }
}
```

別のPCではvaultパスだけ変更すればOK。

### Vault パスの解決順序

1. `--vault` CLI引数（最優先）
2. `SECOND_BRAIN_PATH` 環境変数
3. 自動探索（`~/Documents/second-brain/second-brain`, `~/second-brain` 等）

### オプション

- `--git-sync`: リモートリポジトリとの自動同期を有効化
- 環境変数 `SECOND_BRAIN_GIT_SYNC=true` でも同等

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
