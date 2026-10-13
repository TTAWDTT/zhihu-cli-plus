# zhihu-cli-plus

[![Tests](https://github.com/TTAWDTT/zhihu-cli-plus/actions/workflows/tests.yml/badge.svg)](https://github.com/TTAWDTT/zhihu-cli-plus/actions/workflows/tests.yml)
[![GitHub Release](https://img.shields.io/github/v/release/TTAWDTT/zhihu-cli-plus)](https://github.com/TTAWDTT/zhihu-cli-plus/releases)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://github.com/TTAWDTT/zhihu-cli-plus/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue)](LICENSE)

独立维护的知乎命令行增强版。支持 JSON、游标分页、评论与回复、评论点赞和签名请求。
Python 分发包名从 0.6.0 起为 **`zhihu-cli-plus`**，终端命令仍是 **`zhihu`**，Python 模块仍是 `zhihu_cli`。

项目基于 [BAIGUANGMEI/zhihu-cli](https://github.com/BAIGUANGMEI/zhihu-cli) 的代码发展，保留原始提交历史、作者信息和 Apache-2.0 许可。Plus 自行维护接口、测试与发布；来源说明见 [NOTICE](NOTICE)，版本变化见 [CHANGELOG](CHANGELOG.md)。

## 安装与升级

目前通过 GitHub 安装。独立 PyPI 发布尚待账号配置完成；`pipx install zhihu-cli-plus` 暂时不是已验证的安装方式。

```bash
# 安装主分支
pipx install 'git+https://github.com/TTAWDTT/zhihu-cli-plus.git@main'
zhihu --version

# 升级同一安装源
pipx upgrade zhihu-cli-plus

# 或从 Releases 下载 wheel 后安装
pipx install ./zhihu_cli_plus-0.6.0-py3-none-any.whl
```

如已安装上游或 0.5.x 及更早的 Plus，它们的分发包名是 `pyzhihu-cli`，迁移时用：

```bash
pipx uninstall pyzhihu-cli
pipx install 'git+https://github.com/TTAWDTT/zhihu-cli-plus.git@main'
zhihu --version
```

新旧包都提供 `zhihu` 命令并使用 `zhihu_cli` 模块，因此应替换安装。既有登录文件保持在 `~/.zhihu-cli/`，不放在 Python 虚拟环境里。
如需固定版本，可将 `@main` 换成已经发布的标签；0.5.x 的历史安装包仍使用旧分发包名。

## 登录

```bash
zhihu login --qrcode
zhihu status --json
zhihu whoami --json
```

`status` 检查本地登录文件，`whoami` 在线验证账号。Cookie 只存于本机，不应放进代码仓库、日志、PR 或发布附件。

## 查询与分页

所有子命令均支持 `--json`。查询保留接口字段及 HTML 正文，错误和进度写入 stderr，失败返回非零退出码。

```bash
zhihu search 'Python' --limit 5 --answers 0 --json
zhihu hot --limit 5 --json
zhihu question <问题ID> --answers --limit 5 --json
zhihu answer <回答ID> --comments --limit 5 --json
zhihu feed --limit 5 --json
zhihu feeds --limit 3 --comment-limit 5 --json
zhihu user <用户url_token> --json
zhihu user-answers <用户url_token> --all --json
```

支持分页的集合提供 `--all`、`--offset`、`--next-url`、`--max-pages`。默认最多 100 页，按服务器的 `paging.next` 原样续读；达到页数上限、重复游标或后续请求失败会明确报错。
`--limit` 控制每页数量。兼容旧行为：`answer --comments --limit 0` 获取全部根评论，`feeds --comment-limit 0` 隐藏评论。

## 评论、回复和点赞

```bash
# 读取根评论及接口返回的内嵌回复；类型支持 answer/article/pin/question
zhihu comments <内容ID> --type article --all --json
zhihu comment-detail <评论ID> --json
zhihu replies <根评论ID> --all --json

# 发布前离线预览，去掉 --dry-run 才会实际写入
zhihu comment <内容ID> '评论正文' --type article --dry-run --json
zhihu reply <内容ID> <实际回复的评论ID> '回复正文' --type article --dry-run --json
zhihu comment <内容ID> --file comment.txt --type article --dry-run --json

# 点赞与取消点赞，根评论和子评论都使用实际评论 ID
zhihu like-comment <评论ID> --dry-run --json
zhihu like-comment <评论ID> --json
zhihu like-comment <评论ID> --undo --json

# 删除自己的评论
zhihu delete-comment <评论ID> --dry-run --json
```

`comments --all` 翻根评论页；未随根评论返回的子评论用 `replies 根评论ID --all` 获取。
终端把作者、ID 与正文分组，显示回复目标和当前账号的点赞状态。
`reply` 必须同时指定原内容 ID 和实际目标评论 ID；回复子评论时不能改用根评论 ID。

纯文本会转义为 HTML；只有 `--html` 显式启用时才直接提交 HTML。评论写入、删除、点赞和取消点赞均签名、携带 CSRF 头，写操作不自动重试。
超时或响应不明确时应读回检查，再决定是否重试。API 接收不等于评论已公开展示，审核和仅自己可见的字段会保留。

## 0.7：内容工作流与浏览历史

新增独立详情、用户内容、收藏管理、文章/想法点赞、关注动态、草稿、回答发布/修改及浏览历史命令。查询保留原始 JSON；集合支持相同的游标分页。

```bash
zhihu article-detail <文章ID> --json
zhihu pin-detail <想法ID> --json
zhihu user-pins <url_token> --all --json
zhihu user-questions <url_token> --json
zhihu user-activities <url_token> --json
zhihu user-collections <url_token> --json
zhihu follow-feed --json

zhihu collection-detail <收藏夹ID> --json
zhihu collection-items <收藏夹ID> --all --json
zhihu content-collections <内容ID> --type article --json
zhihu create-collection '收藏夹名称' --dry-run --json
zhihu collect <收藏夹ID> <内容ID> --type article --dry-run --json
zhihu collect <收藏夹ID> <内容ID> --type article --undo --dry-run --json
zhihu delete-collection <收藏夹ID> --dry-run --json

zhihu like-article <文章ID> --dry-run --json
zhihu like-pin <想法ID> --undo --dry-run --json
zhihu follow-user <url_token> --dry-run --json
zhihu notification-list --category follow --json
zhihu notifications-read --category default --dry-run --json

zhihu drafts --type article --json
zhihu draft-versions <内容ID> --type article --json
zhihu draft-detail <草稿ID> --json
zhihu save-draft <问题ID> '回答草稿' --dry-run --json
zhihu save-draft <问题ID> --file answer.html --html --update --dry-run --json
zhihu save-draft <文章ID> '文章草稿' --type article --dry-run --json
zhihu publish-answer <问题ID> '回答正文' --dry-run --json
zhihu edit-answer <回答ID> '修改后的正文' --dry-run --json
zhihu delete-answer <回答ID> --dry-run --json

zhihu history --limit 20 --json
zhihu history --all --max-pages 10 --json
zhihu history-add <内容ID> --type article --dry-run --json
zhihu history-touch <内容ID> --type article --state read --dry-run --json
zhihu history-clear --dry-run --json
```

用户列表省略 `url_token` 时查询当前账号。新收藏夹默认私有，`--public` 显式公开。
收藏增删、删除收藏夹和修改/删除回答会校验所属账号；保存文章草稿也校验文章所有者。
`save-draft --type answer` 的 ID 是问题 ID，默认创建新草稿；已有草稿时必须显式 `--update`。
收藏夹标题最多 20 字、描述最多 60 字（与网页一样按 UTF-16 长度计算）。知乎禁止给自己的文章和想法点赞。
保存操作先读取草稿状态，实际 `delta_time` 根据旧正文长度计算；离线预览只包含新正文长度，不能证明远端草稿状态。

所有新增写操作提供 `--dry-run`，携带签名和当前 CSRF，单次发送、不自动重试。
响应没有明确确认，或返回 ID/状态不符，会报告未确认；嵌套发布结果中的审核信息仍保留。
浏览历史查询和普通内容查询不自动添加历史或上报触达。`history-clear`、通知全部已读、删除收藏夹/回答需确认，`--yes` 可跳过；这些操作不能通过模拟测试获得真实执行许可。

## 其他命令

```bash
zhihu vote <回答ID> --json
zhihu vote <回答ID> --undo --json
zhihu follow-question <问题ID> --json
zhihu collections --json
zhihu notifications --json
zhihu --help
zhihu <子命令> --help
```

发布文章、想法、问题及删除自己的内容等命令可通过 `--help` 查看参数。供 Agent 使用的安装和操作说明在 [skill/SKILL.md](skill/SKILL.md)。

## 开发与验证

```bash
python -m venv .venv
.venv/bin/python -m pip install -e . pytest ruff build twine
.venv/bin/python -m pytest -q
.venv/bin/ruff check zhihu_cli tests
.venv/bin/ruff format --check zhihu_cli tests
.venv/bin/python -m build
.venv/bin/python -m twine check dist/*

# 只读线上测试，使用已有本地登录；请求保护器只允许 GET
.venv/bin/python -m pytest -m 'integration and not integration_write and not integration_like' --live tests/test_live.py
# 新增工作流的只读实测
.venv/bin/python -m pytest -m integration --live tests/test_live_workflows.py
```

普通测试阻止真实网络。真实评论写入、点赞测试分别需要专门的批准标志和指定目标，默认不执行。
工作流真实写入测试另需 `--live-workflow-write`、指定的自有文章/想法/问题 ID 和唯一测试标记；请求保护器只允许预定的临时资源及恢复操作。
0.5.0 已验证 451 个离线测试、38 个线上只读测试，以及经批准并恢复状态的真实评论点赞测试。

## 发布

GitHub Releases 提供 wheel、源码包和 SHA-256 校验文件。独立 PyPI 包通过手动触发的 Trusted Publishing 工作流发布，首次使用需要 PyPI 账号配置发布者；配置与迁移记录见 [docs/project-independence.md](docs/project-independence.md)。

许可全文在 [LICENSE](LICENSE)。签名模块引用的 MIT 来源及许可全文保留在 `zhihu_cli/signing.py`。
