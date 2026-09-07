# obsidian-chromadb-self-evolving-kb 代码审查报告

> 审查对象：`github.com/jialangli/obsidian-chromadb-self-evolving-kb` @ `1803c93`
> 代码规模：4078 行 Python / 22 个模块
> 审查日期：2026/09/07

架构本身是好的 —— 五层分层清晰、"Vault 是唯一事实源"、候选集合隔离、dry-run 默认、双重门禁，这些设计决策都站得住。
问题集中在**代码与文档/配置之间的接线错误**，以及**示例库与评估集不匹配**。以下按严重程度分级。

---

## P0 · 阻断级（按 README 操作必然失败）

### 1. CLI 全部入口失效 —— 4 个 entry point 指向不存在的函数

`pyproject.toml` 声明：

```toml
[project.scripts]
kb-sync  = "kb_engine.sync_obsidian_to_chroma:main"
kb-api   = "kb_engine.kb_api_server:main"
kb-audit = "kb_engine.kb_audit:main"
kb-eval  = "kb_engine.eval_retrieval:main"
```

实际这四个模块**都没有 `main()`**（已逐个 grep 确认，只有 `cli.py` 和 `kb_api_guard.py` 有）。
`cli.py` 内部同样是 `from kb_engine.sync_obsidian_to_chroma import main` → ImportError。

**后果**：`kb sync` / `kb audit` / `kb eval` 三个子命令全崩；`kb-api` 等四个 console script 装了也用不了。
README 把统一 CLI 作为主推入口，等于首屏就是坏的。

**修复**（在三个模块各加）：

```python
def main():
    import sys
    full_rebuild = "--full" in sys.argv
    sync(full_rebuild=full_rebuild)
```

`kb_api_server.py` 则把 `__main__` 块的 argparse + uvicorn.run 抽成 `main()`。

---

### 2. `quickstart.py` 必然失败

第 60 行 `from kb_engine.sync_obsidian_to_chroma import main as sync_main` —— 同样是上一条的受害者。
异常被 `try` 吞掉后 `sys.exit(1)`，用户看到的是一坨 traceback。README 的「3 分钟跑通示例」跑不通。

---

### 3. `PROJECT_ROOT` 算错一层 —— 用户配置静默失效

`src/kb_engine/config.py:18`

```python
PROJECT_ROOT = Path(__file__).resolve().parent.parent   # → <root>/src   ❌
```

文件在 `src/kb_engine/config.py`，`.parent` = `src/kb_engine`，`.parent.parent` = `src`。
应为 `.parent.parent.parent`。

**三重后果**：

| 配置项 | 实际落点 | 期望落点 |
|---|---|---|
| `config.yaml` | `src/config.yaml` | `<root>/config.yaml` |
| `data/` | `src/data/` | `<root>/data/` |
| `vault_path` 默认 | `src/examples/vault`（不存在） | `<root>/examples/vault` |

最坑的是第一条：用户按 README 把 `config.yaml` 放项目根、改了 `vault_path`，**代码读不到，也没有任何报错**（`_load_yaml` 里 `if yaml_path.exists()` 直接跳过）。这是最难自查的一类 bug。

---

### 4. MCP Server 依赖版本错配 —— 起不来

`kb_mcp_server.py:41`

```python
from mcp.server.mcpserver import MCPServer
```

这是 **MCP SDK v2** 的 API（v2 把 `FastMCP` 改名为 `MCPServer`，模块从 `mcp.server.fastmcp` 迁到 `mcp.server.mcpserver`）。
但依赖写的是 `mcp>=1.0.0`（requirements.txt 与 pyproject.toml 一致）。v2 目前仍是 alpha，pip **不会**自动选 pre-release，装到的是 v1.x —— 里面没有这个模块。

**修复**（二选一）：

```python
# 方案 A：留在 v1（推荐，v2 还是 alpha）
from mcp.server.fastmcp import FastMCP
server = FastMCP("kb-engine", instructions=...)

# 方案 B：升到 v2
# pyproject: "mcp>=2.0.0a1,<3"
```

---

### 5. 反馈飞轮断链 —— 硬编码 `D:\kb-engine`

`kb_mcp_server.py:54-55`

```python
TRACE_PATH    = Path(r"D:\kb-engine\logs\mcp_trace.jsonl")
FEEDBACK_PATH = Path(r"D:\kb-engine\logs\feedback.jsonl")
```

完全绕过了 `closed_loop_config`。巧的是 `closed_loop_config.py:21` 也硬编码 `KB_ROOT = Path(r"D:\kb-engine")`，两者在作者机器上恰好对齐，所以本地能跑通 —— 但：

- 任何 clone 项目的人，MCP 反馈会写到 `D:\kb-engine`（Windows 上凭空建目录；Linux/macOS 上会创建一个名字含反斜杠的怪目录）
- ersonal path 进了开源仓库
- 整个自进化闭环无法开箱即用

**修复**：两处都改成读 `settings` / `cfg`，并支持 `KB_ROOT` 环境变量覆盖。

---

### 6. 离线评估闸门在开源示例库上**永远不可能通过**

`eval_retrieval.CASES` 有 12 条，gold 指向 10 个不同文件。
`examples/vault/` 实际只有 **3 篇**：

```
examples/vault/产品/智能设备产品手册.md
examples/vault/技术/向量检索原理.md
examples/vault/方法论/知识库治理方法论.md
```

能命中的只有 2 条（`产品/智能设备产品手册`、`技术/向量检索原理`；
连 `方法论/知识库治理指南` 都对不上 `方法论/知识库治理方法论`）。

Hit@5 天花板 ≈ **17%**，而闸门要求 `EVAL_MIN_HIT5_PROMOTE_FLOOR = 0.85`
（`closed_loop_config.py:52`）→ 闭环**永远**走到 `rollback_offline`，「自进化」在仓库默认状态下是个死循环。

讽刺的是 `CASES` 的注释第 1 条就写着「gold 必须可达 —— 库中确实存在该子串，否则永久无法命中」。作者本机 vault 有完整文件，但没同步进仓库。

**修复**：把缺失的 7 篇示例笔记补进 `examples/vault/`，或把 CASES 裁剪到与示例库匹配的规模（并说明真实使用时需自建 gold 集）。

---

## P1 · 明显缺陷

### 7. `/sync` 硬编码 Windows venv 路径

`kb_api_server.py:330-338` 拼 `venv/Scripts/python.exe`，macOS / Linux / Docker 上必然 `FileNotFoundError`。
README 和 Makefile 都声称支持 macOS/Linux。改用 `sys.executable`。

### 8. API `/search` 的 N+1 查询

`kb_api_server.py:211` —— 混合检索的 `hit` 里已经有 metadata 了，却又对每个结果调一次 `coll.get(ids=[h["id"]])`。
`top_k=50` 就是 50 次 ChromaDB 往返，纯粹浪费。
**修复**：在 `HybridRetriever._make_hit` 里直接返回 `status` / `tags`。

### 9. 同步后检索器缓存不失效

`/sync` 只重置了 `_embedder` / `_collection`，**没清 `_hybrid` 和 `closed_loop_runtime._HUBS`**。
同步完继续用旧索引，直到重启进程。应调 `closed_loop_runtime.reset_hubs()` 并把 `_hybrid = None`。

### 10. 微调温度默认值不一致 —— 「训了等于没训」

`fine_tune_bge.py:44-45` 的 docstring 写得很清楚：

> `scale`：余弦相似度缩放（InfoNCE 温度倒数）… 此前用 1.0 导致 logits 挤在 [-1,1]、梯度极弱，是「训了等于没训」的主因之一。

`train()` 默认已改成 `scale=20.0`。但 `closed_loop.run(scale=1.0)`（closed_loop.py:46）和 CLI `--scale` 默认 1.0，**显式传下去覆盖了正确的默认值**。
闭环实际仍在用被自己认定为 bug 的参数。

**修复**：`closed_loop.run` 的 `scale` 默认改为 `None`，`None` 时不传参让 `train()` 用自己的默认。

### 11. `filter_knowledge_base` 注释与代码自相矛盾

`kb_mcp_server.py:351-352`：

```python
# where_document 与 where 不能同时传给 get()，contains 时在 Python 侧兜底
if contains and not kwargs.get("where_document") and contains not in doc:
```

上一行明明刚 `kwargs["where_document"] = {...}`，下一行的兜底条件 `not kwargs.get("where_document")` 恒为 False —— 兜底是死代码，实际行为是同时传给 Chroma。需要确认 Chroma 的行为后二选一：要么分开查，要么保留同时传并删掉死代码。

### 12. `structural_bootstrap_triples` 写了但没接进主线

`feedback_dataset.py:206` 这个函数注释里声称能「克服小样本 bootstrap 微调≈加噪导致闸门回滚的问题」—— 正是当前闭环最痛的点。但 `closed_loop.run` 里只调用了 `build_training_triples` 和 `bootstrap_triples_from_cases`，**从没调用它**。要么接进去，要么删掉。

另外 `make_training_examples()` 只生成 `(query, pos)` 二元组，把前面辛苦采样的难负例全丢了。

### 13. 反馈处理里的 N×M 次 ChromaDB 客户端创建

`feedback_dataset.fetch_chunk_text` 每次调用都 `chromadb.PersistentClient(path=CHROMA_PATH)`，而它在 `bootstrap_triples_from_cases` 的循环里被调用 12 case × 20 hit = 240 次。
**修复**：client 复用（模块级缓存），或一次性 `col.get()` 拉全量到 dict 再查。

---

## P2 · 架构与可扩展性

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| 14 | **BM25 无倒排索引** | `hybrid_retrieve.py:72-90` | `score()` 遍历全部文档；`_load()` 把整个语料 tokenize 成 2-gram+3-gram 常驻内存（约原文 5-6 倍）。1 万 chunk × 1500 字会内存爆掉。应建 `term → [(doc_idx, tf)]` 倒排表，只算命中 term 的文档 |
| 15 | 向量通道全量 Python 循环 | `hybrid_retrieve.py:249-251` | `for di in range(len(self.ids)): if self._match(...)` 逐条判元数据，可用 numpy bool mask 向量化 |
| 16 | 「增量同步」名存实亡 | `sync_obsidian_to_chroma.py:297` | docstring 称基于 mtime 检测，实际每次全量重训 TF-IDF+SVD + 全量重写。应做 hash/mtime 缓存只处理变动文件 |
| 17 | `chunk_size` / `chunk_overlap` 是死配置 | `config.py:43-44` | 定义了 1500/200，`parse_markdown_to_chunks` 完全没用。长 section 整块入库，超 bge 512 token 会被截断丢语义 |
| 18 | chunk_id 冲突风险 | `sync_obsidian_to_chroma.py:197` | `md5(rel_path + header_path + text[:100])` 只取前 100 字。模板化笔记里前 100 字相同的 section 会 id 撞车，后者静默覆盖前者。应 hash 全文或拼 `chunk_index` |
| 19 | 测试覆盖严重不足 | `tests/` 共 162 行 | 4000 行代码；5 个闭环模块 + `cli.py` + MCP server **零覆盖**。而 `test_config_module_importable` 只 `import` 不调用 —— 所以上面所有 P0 问题全部漏网，CI 是绿的，给人「工程完备」的错觉 |
| 20 | 两套配置体系、阈值不同源 | `config.py` vs `closed_loop_config.py` | `config.hit_at_5_threshold=0.6`，`closed_loop_config` 里却是 `EVAL_MIN_HIT5_FLOOR=0.6` + `EVAL_MIN_HIT5_PROMOTE_FLOOR=0.85` + `EVAL_REGRESSION_TOLERANCE=0.02`。应收敛到一处 |
| 21 | README 与实现不一致 | README MCP 工具表 | 写的 5 个工具里 3 个对不上：写了 `get_knowledge_entry`（不存在）、`mark_feedback`（实际是 `record_retrieval_feedback`）、漏了实际存在的 `feedback_stats`。API 端点表也只列了 4 个（实际 6 个） |
| 22 | CORS + 无鉴权 | `kb_api_server.py:58-64` | `allow_origins=["*"]` 配 `allow_credentials=True`（Starlette 本就不允许这个组合），且 `/sync` 无鉴权，能访问 8300 端口的人就能触发子进程。本地服务风险低，但属不良实践 |
| 23 | import 即创建目录 | `config.py:95-104` | `settings = Settings()` 是模块级单例，`_ensure_dirs()` 在 import 时就建 3 个目录。测试/CI 环境污染，应改懒创建 |
| 24 | 闭环版本号只增不减 | `closed_loop_config.py:98` | `next_model_version()` 基于 `load_active()["version"]`，但只有 `ab_monitor.promote_full` 会 +1，回滚不减。反复失败重试会堆出 v1/v2/v3… 目录 |

---

## 一个架构层面的建议

现在的混合检索是「BGE 向量 + BM25 → RRF 硬融合」（k=60，两通道权重 1:1 写死）。
如果想再往上提一档召回质量，收益最大的不是继续调 RRF，而是**加一个两阶段 rerank**：

```
BGE 向量 ─┐
          ├─ RRF ─→ Top-50 候选 ─→ bge-reranker-base ─→ Top-5
BM25 ─────┘
```

RRF 是无监督融合，看不出 query 和 doc 的细粒度交互；cross-encoder reranker 在 Top-50 上打分，中文场景 Hit@5 通常能有 8-15 个百分点的提升，且 Top-50 → Top-5 的 rerank 在 CPU 上单条也就几十毫秒。

同时建议把 `RRF_K`、vector/bm25 权重做成可配置项 —— 现在全硬编码在 `hybrid_retrieve.py:39-43`。

---

## 建议的修复顺序

```
第一批（半天）：#3 PROJECT_ROOT → #1 补 main() → #2 quickstart → #4 mcp 版本 → #6 补示例库
   ↑ 这五个修完，项目从「clone 下来跑不通」变成「README 说的都能跑」

第二批（一天）：#5 去硬编码路径 → #10 scale → #9 缓存失效 → #8 N+1 → #7 sys.executable

第三批：#21 对齐文档 → #19 补测试（优先闭环链路 + CLI 冒烟）→ #14 BM25 倒排 → #16 真增量
```

**最小验证**：修完后在干净环境跑一遍
`pip install -e ".[dev]" && python quickstart.py && kb eval && kb stats`
这四条现在没有一条能顺利走完。
