# Compatibility Policy

## Python 与源码版本

截至 2026-08-11，`0.4.0` 稳定源码里程碑要求 Python 3.11–3.14；`requires-python`
的下限是 3.11。当前工作流在 Linux 上覆盖 3.11–3.14，并在 Windows 上运行明确列出的
存储、生命周期、构建产物和 Demo smoke；这不代表未列出的操作系统/解释器组合已经
验证。`0.4.0b1` 已在
`f6dca322379c4ea88320c69d752cab471d035e95` 接受为源码基线。历史 `0.4.0a8`
仍是最后一个承诺 Python 3.9 的发布；不会回写它的 wheel、sdist、标签或文档来伪造
新的兼容范围。`0.5.0a2` 已作为 alpha 包发布；后续源码复现仍应固定 full commit SHA，
稳定包支持承诺留到 `1.0`。

项目仍处于 `0.x`：补丁版本优先保持兼容，次版本可以有经过说明和迁移支持的受控
变化。已弃用 API 原则上至少真实警告一个次版本。`remember()` 与接收 transient
Source Turn 的 `adjudicate_relationship_candidates()` 在 b1 发出
`DeprecationWarning`；删除延后到未来明确的不兼容里程碑。新集成应分别使用 Turn Recording +
`archive_turn()`，以及 `adjudicate_turn_candidates()` /
`process_relationship_turn()`。持久历史不会因 Python 入口弃用而删除。

`ERIIEngine()` 不自动启动隐藏线程。后台处理的启动、消费、drain 与关闭始终由宿主
显式控制。

## 独立版本轴

机器可读真相是 `erii.compatibility.COMPATIBILITY_CATALOG`：

| 格式/运行时 | 当前值 | 当前 Reader 接受 |
| --- | --- | --- |
| Package metadata | `0.5.0a3` source identity | 活跃 alpha 源码线；最新上传的 alpha 包是 `0.5.0a2` |
| Python | `>=3.11`，测试至 `3.14` | 3.11–3.14 |
| SQLite | schema `11` | `0`–`11` 可识别；旧 schema 不由 Storage 自动升级 |
| FileStorage | format `2` | `legacy`, `1`, `2` |
| MemoryPack | `0.5.0a3` | `0.1.0`, `0.2.0`, `0.4.0`, `0.4.0a2`–`0.4.0a8`, `0.5.0a1`–`0.5.0a3` |
| Lifecycle Backup | `1` | `1` |
| Lifecycle Plan | writer `3` | readers `1`, `2`, `3` |

这些轴分别演进。包版本不会自动把 MemoryPack 重命名为相同版本，也不自动改变 SQLite
schema、提取器 schema、评估器或关系策略版本。

## “可读、恢复、升级、导入”不是一件事

| 动作 | 含义 | 当前保证 |
| --- | --- | --- |
| inspect/readable | 能识别并严格校验格式身份 | 不等于 Storage 可以直接打开或数据已升级 |
| backup | 捕获完整逻辑 payload 到 Backup v1 | 保持被检测到的原格式；不迁移 |
| restore | 从 Backup v1 发布到缺失目标 | 保持原格式/身份；不覆盖、不升级 |
| upgrade | 把具有已验证路线的旧格式转换到当前格式 | verified backup-first、源保留、并排目标；不等于所有可识别版本都可升级 |
| import | 把 MemoryPack 语义写入全新 Storage | 隔离 staging 后发布；不是 live merge |

因此，把旧 SQLite 的 backup restore 到新路径后，它仍是旧 schema；必须再使用
`UpgradeRequest`。同样，MemoryPack upgrade 改变 Pack wire，MemoryPack import 才会
创建 FileStorage/SQLite。`ERIIEngine.import_memory(overwrite=True)` 的在线合并语义
不等于 lifecycle fresh import，也不提供全库原子替换。

## 当前数据生命周期支持矩阵

- `LifecycleInspector` 对 FileStorage、SQLite、MemoryPack 和 Lifecycle Backup
  零写入地返回 `missing | empty | current | migration_required`、版本、文件数、警告和
  不含正文的内容指纹。
- FileStorage、SQLite 与 MemoryPack 都可以创建 Lifecycle Backup v1，并恢复到同种
  live target 的缺失路径。
- Backup v1 中 producer-relative 的 FileStorage v1、SQLite v9、MemoryPack
  `0.4.0a8`、`0.5.0a1` 与 `0.5.0a2` current/version/status 身份会先按冻结的旧版本
  目录校验，再按当前目录重新分类并验证 payload；这也覆盖当时的 migration-required
  来源以及 FileStorage/SQLite empty 来源。这里的 `0.5.0a1` 身份也覆盖已发布
  `0.5.0a2` 制品的实际 writer。未知 producer 目录或不匹配的 status/version 仍会失败关闭。
- FileStorage `legacy → 2` 与 `1 → 2`、SQLite `6 | 9 | 10 → 11`，以及所有已声明
  的旧可读 MemoryPack → `0.5.0a3` 有显式 `UpgradeRequest` 路线。
- SQLite schema `0`–`5`、`7`、`8` 虽可由 inspector 识别，但当前版本没有为它们声明经过
  fixture 验证的 lifecycle upgrade 路线；不得把“可识别”写成“可升级”。
- 当前 Storage 构造不会把旧 SQLite 作为隐式迁移入口；需要升级的 schema 失败关闭并
  要求使用 lifecycle 流程。
- `MemoryPackImportRequest` 可以把 current 或 declared-readable Pack 原子发布到全新
  FileStorage v2 或 SQLite v11；已存在目标和在线 merge 不支持。
- 当前 FileStorage v2 由规范 `.erii-store.json` 声明。没有 manifest 的历史目录会被
  lifecycle inspector 识别为 `legacy`；inspect、backup 和 restore 不会偷偷赋予新身份。
- Backup-first erase 支持关系、Source Turn、Relationship Event 与完整用户四种范围；
  relationship rebuild 从剩余权威历史重算派生投影。两者只支持 current FileStorage v2
  与 SQLite v11。

新计划使用 contract v3，绑定策略、来源指纹、目标父目录、可选 pre-change backup 与
selector。严格 reader 仍按原字段和摘要规则读取/执行 v1 backup/restore 与 v2 既有操作；
旧计划不能声明新操作或携带 v3 selector。

## MemoryPack 权威兼容

当前 MemoryPack `0.5.0a3` 携带规范 Source Turn、Review/Delivery Record、归档 Artifact
Evidence 与 tombstone commitments、Relationship Event、Persona Reflection 和持久
Relationship Processing Run；当前 reader 继续读取 `0.4.0a8`。现代 schema `"2"`
产物在首次写入前必须闭合到精确
Source Turn，并匹配类型、稳定 ID 和规范载荷摘要。

旧 Pack 可读不代表缺失的现代权威会被补造。升级与导入不会从当前摘要猜测消息级
来源、成功连续性审查、角色、Persona Growth 或零产物决定。绑定 Source Turn、正式
反思或关系处理历史的 Pack 继续受原 `Agent × User × relationship_id` 约束；提供新 ID
或 `overwrite=True` 不能成为跨关系搬运许可。

Episode 与 Relationship Chapter 是可重建投影，不进入 MemoryPack 权威 wire。关系
处理 run 也不复制完整 Prompt、模型推理或私有人设原文。

## Backup 与失败语义

Lifecycle Backup v1 是严格目录包：manifest 绑定 operation/plan identity、原存储格式、
完整 payload 文件集合、大小和 SHA-256。FileStorage 只排除已知的运行时锁；其他
`.lock` 文件仍是逻辑数据。遗留 `.tmp`、符号链接、junction/reparse point、硬链接或
非普通文件失败关闭。SQLite 需要静止 WAL/journal；MemoryPack 先检查大小和 envelope。

发布使用 missing-target/no-replace 语义。升级、删除和重建必须先产生匹配计划的 verified
backup。相同 plan 只有在产物精确匹配时返回 `already_complete`。最终验证失败后已经
可见的目标会被保留供人工检查，避免误删发布后其他宿主写入。

File/tree 复制与哈希采用至多 1 MiB 分块；SQLite 语义摘要流式遍历规范行。MemoryPack
生命周期输入上限 256 MiB，需要物化的 transform 上限 512 MiB，backup manifest 上限
16 MiB。

## 安全兼容边界

Plan digest、payload SHA-256、MemoryPack commitment 与 semantic digest 只能检测内部
损坏和执行漂移，不是签名、MAC 或来源认证。所有内置持久格式默认明文。跨进程锁只
协调遵守协议的可信宿主，不是授权、多租户隔离或对抗性同机文件系统边界。删除报告也
不会声称已经自动删除 lifecycle backup、外部向量库、导出 Pack、日志、远程服务或其他
副本。

完整边界见 [`../SECURITY.md`](../SECURITY.md)，可执行流程见
[`data-lifecycle.md`](data-lifecycle.md)。

## 可选组件

只有 CI 或文档明确列出的组合视为经过验证。OpenAI SDK、FastAPI、ChromaDB 和第三方
Storage/Agent 框架由宿主选择并承担其版本、授权、网络与数据处理边界。长期政策是覆盖
仍处于 Python 官方安全维护期的运行时，不永久维护已经结束支持的版本。
