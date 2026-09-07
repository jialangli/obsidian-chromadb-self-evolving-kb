#!/usr/bin/env python3
"""
快速入门脚本 —— 一键搭建示例知识库

功能：
  1. 检查 Python 环境和依赖
  2. 使用示例 Vault 构建向量索引（LSA 模式，无需下载模型）
  3. 演示混合检索效果

用法：
  python quickstart.py
"""
import os
import sys
import subprocess


def check_python():
    """检查 Python 版本"""
    print("🐍 检查 Python 环境...")
    print(f"   版本: {sys.version.split()[0]}")
    if sys.version_info < (3, 9):
        print("   ⚠ 需要 Python 3.9+")
        return False
    print("   ✓ Python 版本 OK")
    return True


def check_deps():
    """检查依赖是否安装"""
    print("\n📦 检查依赖...")
    required = [
        ("chromadb", "chromadb"),
        ("frontmatter", "python-frontmatter"),
        ("sklearn", "scikit-learn"),
        ("numpy", "numpy"),
    ]
    missing = []
    for module, pkg in required:
        try:
            __import__(module)
            print(f"   ✓ {pkg}")
        except ImportError:
            print(f"   ✗ {pkg} (未安装)")
            missing.append(pkg)
    if missing:
        print(f"\n缺少 {len(missing)} 个依赖，运行: pip install {' '.join(missing)}")
        return False
    return True


def build_index():
    """构建示例索引"""
    print("\n🏗  构建向量索引（LSA 模式）...")
    src_dir = os.path.join(os.path.dirname(__file__), "src")
    sys.path.insert(0, src_dir)
    os.chdir(os.path.dirname(__file__))  # 切换到项目根目录

    try:
        from kb_engine.sync_obsidian_to_chroma import main as sync_main
        # 模拟命令行参数
        sys.argv = ["kb-sync", "--full"]
        sync_main()
        print("   ✓ 索引构建完成")
        return True
    except Exception as e:
        print(f"   ✗ 构建失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def demo_search():
    """演示检索"""
    print("\n🔍 演示混合检索...")
    src_dir = os.path.join(os.path.dirname(__file__), "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    os.chdir(os.path.dirname(__file__))

    try:
        from kb_engine.hybrid_retrieve import HybridRetriever
        retriever = HybridRetriever()

        queries = [
            "智能设备有哪些功能",
            "向量检索的原理是什么",
            "如何治理知识库",
        ]
        for q in queries:
            print(f"\n   查询: \"{q}\"")
            hits = retriever.search(q, top_k=3)
            for i, hit in enumerate(hits, 1):
                source = hit.get("source_file", "?")
                score = hit.get("fused_score", 0)
                excerpt = hit.get("excerpt", "")[:60].replace("\n", " ")
                print(f"   {i}. [{score:.3f}] {source}")
                print(f"      {excerpt}...")
        print("\n   ✓ 检索演示完成")
        return True
    except Exception as e:
        print(f"   ✗ 检索失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("=" * 60)
    print("  🚀 Obsidian + ChromaDB 自进化知识检索系统 - 快速入门")
    print("=" * 60)

    if not check_python():
        sys.exit(1)

    if not check_deps():
        print("\n💡 提示: 运行 pip install -r requirements.txt 安装所有依赖")
        sys.exit(1)

    if not build_index():
        sys.exit(1)

    if not demo_search():
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  🎉 快速入门完成！")
    print("=" * 60)
    print("\n下一步：")
    print("  • 修改 config.example.yaml 为 config.yaml，指向你自己的 Vault")
    print("  • 运行 python src/sync_obsidian_to_chroma.py 同步你的知识库")
    print("  • 运行 python src/kb_api_server.py 启动 HTTP API 服务")
    print("  • 配置 MCP Server 接入你的 AI Agent")
    print("\n详细文档见 README.md 或 index.html")


if __name__ == "__main__":
    main()
