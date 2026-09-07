# -*- coding: utf-8 -*-
"""
Knowledge 知识库体检脚本
扫描维度：
  A. frontmatter 结构错误（缺失字段/非法枚举/日期格式）
  B. 过期数据（expiry 已过期的 fact、长期未更新的文件）
  C. 断链（[[wikilink]] 指向不存在的笔记）
  D. 内容异常（空文件/超短文件/重复文件名）
  E. 向量库索引一致性（ChromaDB 中的 source_file 是否仍存在于磁盘）
"""
import re
import sys
from pathlib import Path
from datetime import date, datetime

import frontmatter
from config import settings

VAULT = Path(settings.vault_path)
CHROMA_PATH = settings.chroma_path
TODAY = date.today()

VALID_MEMORY_TYPES = {"fact", "preference", "experience", "task_state", "navigation", "safety"}
VALID_STATUS = {"active", "in_progress", "archived", "deprecated", "draft", "completed", "approved"}

# 每类记忆的必填字段
REQUIRED_FIELDS = {
    "fact": ["memory_type", "source", "date_updated"],
    "preference": ["memory_type", "owner", "date_updated"],
    "experience": ["memory_type", "date_updated"],
    "task_state": ["memory_type", "status", "date_updated"],
    "navigation": ["memory_type", "date_updated"],
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

issues = {"A": [], "B": [], "C": [], "D": [], "E": []}
stats = {"files": 0, "with_fm": 0, "links": 0}

# ── 收集全部 md 文件 ──────────────────────────────────
# 模板目录的占位符属于设计，跳过结构检查
TEMPLATE_DIRS = ("模板",)

md_files = [p for p in VAULT.rglob("*.md")
            if ".obsidian" not in p.parts and ".trash" not in p.parts]
stats["files"] = len(md_files)
all_names = {p.stem for p in md_files}
# Obsidian 链接解析集合：笔记相对路径 + 库中全部文件（含 PDF 等附件）
all_paths = ({str(p.relative_to(VAULT)).replace("\\", "/")[:-3] for p in md_files}
             | {str(p.relative_to(VAULT)).replace("\\", "/")
                for p in VAULT.rglob("*")
                if p.is_file() and ".obsidian" not in p.parts})

# 正文过短（无法产出内容块）的文件，不参与索引一致性比对
def _has_content(p: Path) -> bool:
    try:
        return len(frontmatter.load(str(p)).content.strip()) >= 50
    except Exception:
        return False

parsed = []  # (path, post)

# ── A. frontmatter 结构检查 ──────────────────────────
for p in md_files:
    rel = str(p.relative_to(VAULT))
    is_template = TEMPLATE_DIRS[0] in p.parts
    try:
        post = frontmatter.load(str(p))
    except Exception as e:
        issues["A"].append((rel, f"frontmatter 解析失败: {e}"))
        continue
    parsed.append((p, post))
    fm = post.metadata
    if not fm:
        issues["A"].append((rel, "完全缺失 frontmatter"))
        continue
    stats["with_fm"] += 1
    if is_template:
        # 模板文件：占位符/缺失字段是设计，只检查 memory_type 合法性
        mt = fm.get("memory_type")
        if mt and mt not in VALID_MEMORY_TYPES:
            issues["A"].append((rel, f"非法 memory_type: {mt}"))
        continue

    mt = fm.get("memory_type")
    if not mt:
        issues["A"].append((rel, "缺失 memory_type"))
    elif mt not in VALID_MEMORY_TYPES:
        issues["A"].append((rel, f"非法 memory_type: {mt}"))

    # 必填字段
    for field in REQUIRED_FIELDS.get(mt, ["memory_type"]):
        v = fm.get(field)
        if v is None or (isinstance(v, str) and not v.strip()):
            issues["A"].append((rel, f"memory_type={mt} 缺失必填字段: {field}"))

    # 日期格式
    for field in ("date_created", "date_updated", "expiry"):
        v = fm.get(field)
        if isinstance(v, str) and v and not DATE_RE.match(v):
            issues["A"].append((rel, f"{field} 日期格式错误: {v}（应为 YYYY-MM-DD）"))

    # status 枚举（有值时校验）
    st = fm.get("status")
    if isinstance(st, str) and st and st not in VALID_STATUS:
        issues["A"].append((rel, f"非法 status: {st}"))

    # confidence 范围
    conf = fm.get("confidence")
    if conf is not None:
        try:
            c = float(conf)
            if not (0 <= c <= 1):
                issues["A"].append((rel, f"confidence 超出 [0,1]: {conf}"))
        except (TypeError, ValueError):
            issues["A"].append((rel, f"confidence 非数值: {conf}"))

    # expiry < date_updated 逻辑错误
    exp = fm.get("expiry")
    du = fm.get("date_updated")
    if isinstance(exp, str) and isinstance(du, str) and DATE_RE.match(exp) and DATE_RE.match(du):
        if exp < du:
            issues["A"].append((rel, f"逻辑错误: expiry({exp}) 早于 date_updated({du})"))

# ── B. 过期数据检查 ──────────────────────────────────
for p, post in parsed:
    rel = str(p.relative_to(VAULT))
    fm = post.metadata
    exp = fm.get("expiry")
    if isinstance(exp, str) and DATE_RE.match(exp):
        d = date.fromisoformat(exp)
        if d < TODAY:
            issues["B"].append((rel, f"已过期 {TODAY - d} 天（expiry={exp}），应更新或归档"))
    du = fm.get("date_updated")
    if isinstance(du, str) and DATE_RE.match(du):
        d = date.fromisoformat(du)
        days = (TODAY - d).days
        mt = fm.get("memory_type")
        threshold = {"fact": 180, "experience": 365, "preference": 180,
                     "task_state": 30, "navigation": 180}.get(mt, 180)
        if days > threshold:
            issues["B"].append((rel, f"已 {days} 天未更新（超过 {mt} 类型阈值 {threshold} 天）"))

# ── C. 断链检查 ──────────────────────────────────────
# Obsidian 解析规则：[[a/b]] 匹配任何以 a/b 结尾的路径；[[name]] 匹配文件名
link_re = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")
def resolve(target: str) -> bool:
    if not target:
        return True
    target = target.strip().replace("\\", "/")
    if target in all_paths or target in all_names:
        return True
    return any(fp == target or fp.endswith("/" + target) for fp in all_paths)

for p, post in parsed:
    rel = str(p.relative_to(VAULT))
    for m in link_re.finditer(post.content):
        target = m.group(1).strip()
        stats["links"] += 1
        if not resolve(target):
            issues["C"].append((rel, f"断链 → [[{target}]]"))

# ── D. 内容异常检查（同目录重名才有问题；跨目录的"首页/README"是正常结构）──
for p, post in parsed:
    rel = str(p.relative_to(VAULT))
    body = post.content.strip()
    if len(body) < 50:
        issues["D"].append((rel, f"疑似空壳文件（正文仅 {len(body)} 字符）"))
by_dir = {}
for p, _ in parsed:
    by_dir.setdefault(p.parent, []).append(p.stem)
for d, stems in by_dir.items():
    dup = {s for s in stems if stems.count(s) > 1}
    if dup:
        issues["D"].append((str(d.relative_to(VAULT)), f"同目录重复文件名: {', '.join(sorted(dup))}"))

# ── E. 向量库索引一致性 ──────────────────────────────
try:
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    coll = client.get_collection("kb-engine")
    metas = coll.get(include=["metadatas"])["metadatas"]
    indexed_files = {m.get("source_file", "") for m in metas}
    # 同步脚本排除了 模板 目录，索引对比也应排除
    disk_files = {str(p.relative_to(VAULT)).replace("\\", "/")
                  for p in md_files
                  if TEMPLATE_DIRS[0] not in p.parts and _has_content(p)}
    stale = indexed_files - disk_files
    missing = disk_files - indexed_files
    if stale:
        for f in sorted(stale):
            issues["E"].append((f, "向量库索引了已删除的文件（陈旧索引，需重新同步）"))
    if missing:
        for f in sorted(missing):
            issues["E"].append((f, "新文件未入向量库（需重新同步）"))
except Exception as e:
    issues["E"].append(("-", f"向量库检查失败: {e}"))

# ── 输出报告 ─────────────────────────────────────────
print("=" * 64)
print("Knowledge 知识库体检报告")
print(f"日期: {TODAY}  文件数: {stats['files']}  有frontmatter: {stats['with_fm']}")
print(f"扫描 wikilink: {stats['links']} 条")
print("=" * 64)
titles = {"A": "A. frontmatter 结构错误", "B": "B. 过期/陈旧数据",
          "C": "C. 断链", "D": "D. 内容异常", "E": "E. 向量库索引一致性"}
for key in "ABCDE":
    print(f"\n{titles[key]}  ——  {len(issues[key])} 项")
    for rel, desc in issues[key][:30]:
        print(f"  [{rel}] {desc}")
    if len(issues[key]) > 30:
        print(f"  ... 另有 {len(issues[key]) - 30} 项")

total = sum(len(v) for v in issues.values())
print("\n" + "=" * 64)
print(f"总计发现问题: {total} 项（A:{len(issues['A'])} B:{len(issues['B'])} "
      f"C:{len(issues['C'])} D:{len(issues['D'])} E:{len(issues['E'])}）")

# ── Markdown 报告 ────────────────────────────────────
if "--md" in sys.argv:
    out_path = Path(sys.argv[sys.argv.index("--md") + 1])
    # 断链聚合：按缺失目标去重，统计被引用次数与来源文件
    missing_agg = {}
    for rel, desc in issues["C"]:
        target = desc.split("[[")[-1].rstrip("]]")
        missing_agg.setdefault(target, []).append(rel)
    lines = [
        "# Knowledge 知识库体检报告",
        "",
        f"- 体检日期：{TODAY}",
        f"- 知识库路径：`{VAULT}`",
        f"- 文件总数：{stats['files']}（frontmatter 覆盖率 "
        f"{stats['with_fm']}/{stats['files']}）",
        f"- 扫描双链：{stats['links']} 条",
        "",
        "## 总览",
        "",
        "| 维度 | 问题数 | 说明 |",
        "|------|--------|------|",
        f"| A. 元数据错误 | {len(issues['A'])} | frontmatter 字段缺失/格式/枚举错误 |",
        f"| B. 过期数据 | {len(issues['B'])} | expiry 已过期、超过更新周期 |",
        f"| C. 断链 | {len(issues['C'])} | 双链指向不存在的笔记 |",
        f"| D. 内容异常 | {len(issues['D'])} | 空壳文件、同目录重名 |",
        f"| E. 索引一致性 | {len(issues['E'])} | 向量库与磁盘不一致 |",
        "",
        "## A. 元数据错误",
        "",
    ]
    lines += ([f"- `{r}` — {d}" for r, d in issues["A"]] or ["无 ✅"])
    lines += ["", "## B. 过期数据", ""]
    lines += ([f"- `{r}` — {d}" for r, d in issues["B"]] or ["无 ✅"])
    lines += ["", "## D. 内容异常", ""]
    lines += ([f"- `{r}` — {d}" for r, d in issues["D"]] or ["无 ✅"])
    lines += ["", "## E. 索引一致性", ""]
    lines += ([f"- `{r}` — {d}" for r, d in issues["E"]] or ["无 ✅"])
    lines += [
        "",
        f"## C. 断链分析（{len(issues['C'])} 处引用，指向 {len(missing_agg)} 个不存在的笔记）",
        "",
        "按被引用次数排序：",
        "",
        "| 缺失笔记 | 被引用次数 | 主要来源 |",
        "|----------|-----------|---------|",
    ]
    for target, srcs in sorted(missing_agg.items(), key=lambda x: -len(x[1])):
        uniq = sorted(set(srcs))
        lines.append(f"| `[[{target}]]` | {len(srcs)} | {'、'.join(uniq[:3])}"
                     f"{' 等' if len(uniq) > 3 else ''} |")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nMarkdown 报告已生成: {out_path}")
