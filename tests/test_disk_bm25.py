"""
DiskBM25Index 测试
==================
核心承诺：磁盘倒排与内存版 BM25Index **逐位一致**（同公式同排序），
同时倒排驻留 SQLite、进程不保留词条结构。CI 无数据依赖，纯函数级测试。
"""

import hashlib

from kb_engine.hybrid_retrieve import BM25Index, DiskBM25Index


def _corpus():
    """小中文语料（按空格分词，token 即 2/3-gram 语义单元）。"""
    return [
        "知识库 检索 混合 检索 RRF 融合 向量 语义".split(),
        "BM25 关键词 检索 召回 精确 匹配".split(),
        "自进化 闭环 反馈 微调 灰度 A B 晋升".split(),
        "Obsidian Vault 笔记 同步 ChromaDB 索引".split(),
        "评测 指标 Hit 5 MRR 召回 质量 闸门".split(),
    ]


_QUERIES = [
    "检索 融合",
    "BM25 召回",
    "微调 闭环 晋升",
    "不存在的词 xx yy",
    "检索",
    "Obsidian Vault",
    "ChromaDB",
    "知识库 检索 混合",
]


def _assert_same(mem, disk):
    for q in _QUERIES:
        qt = q.split()
        mem_ranked = mem.score(qt)
        disk_ranked = disk.score(qt)
        assert (
            mem_ranked == disk_ranked
        ), f"query={q!r} 排序不一致\n  内存版: {mem_ranked}\n  磁盘版: {disk_ranked}"


def test_disk_bm25_matches_in_memory_exactly(tmp_path):
    corpus = _corpus()
    mem = BM25Index(corpus)
    disk = DiskBM25Index(corpus, db_path=tmp_path / "bm25.db")
    assert disk.db_path.exists()
    _assert_same(mem, disk)


def test_disk_bm25_no_rebuild_when_corpus_unchanged(tmp_path):
    corpus = _corpus()
    db = tmp_path / "bm25.db"
    DiskBM25Index(corpus, db_path=db)
    digest_before = hashlib.sha256(db.read_bytes()).hexdigest()
    # 再次用相同语料构建：签名命中 → 不重建 → 文件字节不变
    DiskBM25Index(corpus, db_path=db)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == digest_before


def test_disk_bm25_rebuild_when_corpus_changes(tmp_path):
    db = tmp_path / "bm25.db"
    DiskBM25Index(_corpus(), db_path=db)
    new_corpus = [["only", "topic", "火星", "救援"], ["another", "doc", "here"]]
    disk = DiskBM25Index(new_corpus, db_path=db)
    # 新语料里的词应可命中且 docid 按新语料编号
    ranked = disk.score("火星 救援".split())
    assert ranked and ranked[0][0] == 0


def test_disk_bm25_empty_corpus(tmp_path):
    disk = DiskBM25Index([], db_path=tmp_path / "bm25.db")
    assert disk.score(["任意", "词"]) == []
    assert disk.score([]) == []
