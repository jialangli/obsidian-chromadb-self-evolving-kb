"""
Knowledge 知识库体检脚本
扫描维度：
  A. frontmatter 结构错误（缺失字段/非法枚举/日期格式）
  B. 过期数据（expiry 已过期的 fact、长期未更新的文件）
  C. 断链（[[wikilink]] 指向不存在的笔记）
  D. 内容异常（空文件/超短文件/重复文件名）
  E. 向量库索引一致性（ChromaDB 中的 source_file 是否仍存在于磁盘）

用法：
  python -m kb_engine.kb_audit                  # 控制台报告
  python -m kb_engine.kb_audit --md report.md   # 额外输出 Markdown 报告
  kb audit                                      # 等价的 CLI 子命令

设计说明：所有检查逻辑都在 run_audit() 内，模块可安全 import（早期版本把扫描
写在模块顶层，导致 import 即执行一次全量扫描）。
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import frontmatter

from kb_engine.config import settings

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
LINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")

# 模板目录的占位符属于设计，跳过结构检查
TEMPLATE_DIRS = ("模板",)

TITLES = {
    "A": "A. frontmatter 结构错误",
    "B": "B. 过期/陈旧数据",
    "C": "C. 断链",
    "D": "D. 内容异常",
    "E": "E. 向量库索引一致性",
}


def _has_content(p: Path) -> bool:
    """正文过短（无法产出内容块）的文件，不参与索引一致性比对"""
    try:
        return len(frontmatter.load(str(p)).content.strip()) >= 50
    except Exception:
        return False


def _resolve(target: str, all_paths: set, all_names: set) -> bool:
    if not target:
        return True
    target = target.strip().replace("\\", "/")
    if target in all_paths or target in all_names:
        return True
    return any(fp == target or fp.endswith("/" + target) for fp in all_paths)


def run_audit(
    vault_path: str = None,
    chroma_path: str = None,
    collection_name: str = None,
    today: date = None,
) -> dict:
    """
    执行五维体检。
    Returns: {"issues": {A..E: [(rel, desc)]}, "stats": {...}, "vault": Path}
    """
    vault = Path(vault_path or settings.vault_path)
    chroma = chroma_path or settings.chroma_path
    coll_name = collection_name or settings.collection_lsa
    today = today or date.today()

    issues = {k: [] for k in "ABCDE"}
    stats = {"files": 0, "with_fm": 0, "links": 0}

    if not vault.exists():
        issues["A"].append((str(vault), f"Vault 路径不存在：{vault}"))
        return {"issues": issues, "stats": stats, "vault": vault}

    md_files = [
        p for p in vault.rglob("*.md") if ".obsidian" not in p.parts and ".trash" not in p.parts
    ]
    stats["files"] = len(md_files)
    all_names = {p.stem for p in md_files}
    # Obsidian 链接解析集合：笔记相对路径 + 库中全部文件（含 PDF 等附件）
    all_paths = {str(p.relative_to(vault)).replace("\\", "/")[:-3] for p in md_files} | {
        str(p.relative_to(vault)).replace("\\", "/")
        for p in vault.rglob("*")
        if p.is_file() and ".obsidian" not in p.parts
    }

    parsed = []  # (path, post)

    # ── A. frontmatter 结构检查 ──────────────────────────
    for p in md_files:
        rel = str(p.relative_to(vault))
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

        for field in REQUIRED_FIELDS.get(mt, ["memory_type"]):
            v = fm.get(field)
            if v is None or (isinstance(v, str) and not v.strip()):
                issues["A"].append((rel, f"memory_type={mt} 缺失必填字段: {field}"))

        for field in ("date_created", "date_updated", "expiry"):
            v = fm.get(field)
            if isinstance(v, str) and v and not DATE_RE.match(v):
                issues["A"].append((rel, f"{field} 日期格式错误: {v}（应为 YYYY-MM-DD）"))

        st = fm.get("status")
        if isinstance(st, str) and st and st not in VALID_STATUS:
            issues["A"].append((rel, f"非法 status: {st}"))

        conf = fm.get("confidence")
        if conf is not None:
            try:
                c = float(conf)
                if not (0 <= c <= 1):
                    issues["A"].append((rel, f"confidence 超出 [0,1]: {conf}"))
            except (TypeError, ValueError):
                issues["A"].append((rel, f"confidence 非数值: {conf}"))

        exp = fm.get("expiry")
        du = fm.get("date_updated")
        if (
            isinstance(exp, str)
            and isinstance(du, str)
            and DATE_RE.match(exp)
            and DATE_RE.match(du)
            and exp < du
        ):
            issues["A"].append((rel, f"逻辑错误: expiry({exp}) 早于 date_updated({du})"))

    # ── B. 过期数据检查 ──────────────────────────────────
    for p, post in parsed:
        rel = str(p.relative_to(vault))
        fm = post.metadata
        exp = fm.get("expiry")
        if isinstance(exp, str) and DATE_RE.match(exp):
            d = date.fromisoformat(exp)
            if d < today:
                issues["B"].append((rel, f"已过期 {today - d} 天（expiry={exp}），应更新或归档"))
        du = fm.get("date_updated")
        if isinstance(du, str) and DATE_RE.match(du):
            days = (today - date.fromisoformat(du)).days
            mt = fm.get("memory_type")
            threshold = {
                "fact": 180,
                "experience": 365,
                "preference": 180,
                "task_state": 30,
                "navigation": 180,
            }.get(mt, 180)
            if days > threshold:
                issues["B"].append((rel, f"已 {days} 天未更新（超过 {mt} 类型阈值 {threshold} 天）"))

    # ── C. 断链检查 ──────────────────────────────────────
    for p, post in parsed:
        rel = str(p.relative_to(vault))
        for m in LINK_RE.finditer(post.content):
            target = m.group(1).strip()
            stats["links"] += 1
            if not _resolve(target, all_paths, all_names):
                issues["C"].append((rel, f"断链 → [[{target}]]"))

    # ── D. 内容异常检查（同目录重名才有问题；跨目录的"首页/README"是正常结构）──
    for p, post in parsed:
        body = post.content.strip()
        if len(body) < 50:
            issues["D"].append(
                (str(p.relative_to(vault)), f"疑似空壳文件（正文仅 {len(body)} 字符）")
            )
    by_dir = {}
    for p, _ in parsed:
        by_dir.setdefault(p.parent, []).append(p.stem)
    for d, stems in by_dir.items():
        dup = {s for s in stems if stems.count(s) > 1}
        if dup:
            issues["D"].append(
                (str(d.relative_to(vault)), f"同目录重复文件名: {', '.join(sorted(dup))}")
            )

    # ── E. 向量库索引一致性 ──────────────────────────────
    try:
        import chromadb

        client = chromadb.PersistentClient(path=chroma)
        coll = client.get_collection(coll_name)
        metas = coll.get(include=["metadatas"])["metadatas"]
        indexed_files = {m.get("source_file", "") for m in metas}
        # 同步脚本排除了 模板 目录，索引对比也应排除
        disk_files = {
            str(p.relative_to(vault)).replace("\\", "/")
            for p in md_files
            if TEMPLATE_DIRS[0] not in p.parts and _has_content(p)
        }
        for f in sorted(indexed_files - disk_files):
            issues["E"].append((f, "向量库索引了已删除的文件（陈旧索引，需重新同步）"))
        for f in sorted(disk_files - indexed_files):
            issues["E"].append((f, "新文件未入向量库（需重新同步）"))
    except Exception as e:
        issues["E"].append(("-", f"向量库检查失败（集合 {coll_name}）: {e}"))

    return {"issues": issues, "stats": stats, "vault": vault, "collection": coll_name}


def print_report(report: dict, limit: int = 30):
    issues, stats = report["issues"], report["stats"]
    print("=" * 64)
    print("Knowledge 知识库体检报告")
    print(f"日期: {date.today()}  文件数: {stats['files']}  有frontmatter: {stats['with_fm']}")
    print(f"扫描 wikilink: {stats['links']} 条")
    print("=" * 64)
    for key in "ABCDE":
        print(f"\n{TITLES[key]}  ——  {len(issues[key])} 项")
        for rel, desc in issues[key][:limit]:
            print(f"  [{rel}] {desc}")
        if len(issues[key]) > limit:
            print(f"  ... 另有 {len(issues[key]) - limit} 项")

    total = sum(len(v) for v in issues.values())
    print("\n" + "=" * 64)
    print(
        f"总计发现问题: {total} 项（A:{len(issues['A'])} B:{len(issues['B'])} "
        f"C:{len(issues['C'])} D:{len(issues['D'])} E:{len(issues['E'])}）"
    )


def render_markdown(report: dict) -> str:
    issues, stats = report["issues"], report["stats"]
    # 断链聚合：按缺失目标去重，统计被引用次数与来源文件
    missing_agg = {}
    for rel, desc in issues["C"]:
        target = desc.split("[[")[-1].rstrip("]]")
        missing_agg.setdefault(target, []).append(rel)

    lines = [
        "# Knowledge 知识库体检报告",
        "",
        f"- 体检日期：{date.today()}",
        f"- 知识库路径：`{report['vault']}`",
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
    lines += [f"- `{r}` — {d}" for r, d in issues["A"]] or ["无 ✅"]
    lines += ["", "## B. 过期数据", ""]
    lines += [f"- `{r}` — {d}" for r, d in issues["B"]] or ["无 ✅"]
    lines += ["", "## D. 内容异常", ""]
    lines += [f"- `{r}` — {d}" for r, d in issues["D"]] or ["无 ✅"]
    lines += ["", "## E. 索引一致性", ""]
    lines += [f"- `{r}` — {d}" for r, d in issues["E"]] or ["无 ✅"]
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
        lines.append(
            f"| `[[{target}]]` | {len(srcs)} | {'、'.join(uniq[:3])}"
            f"{' 等' if len(uniq) > 3 else ''} |"
        )
    return "\n".join(lines)


def main(argv: list = None):
    """CLI 入口（pyproject: kb-audit = kb_engine.kb_audit:main）"""
    parser = argparse.ArgumentParser(description="Knowledge 知识库体检")
    parser.add_argument("--vault", default=None, help="覆盖 vault_path")
    parser.add_argument("--collection", default=None, help="覆盖索引集合名（默认取 settings）")
    parser.add_argument("--md", default=None, metavar="PATH", help="额外输出 Markdown 报告")
    args = parser.parse_args(argv)

    report = run_audit(vault_path=args.vault, collection_name=args.collection)
    print_report(report)

    if args.md:
        out_path = Path(args.md)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_markdown(report), encoding="utf-8")
        print(f"\nMarkdown 报告已生成: {out_path}")


if __name__ == "__main__":
    main()
