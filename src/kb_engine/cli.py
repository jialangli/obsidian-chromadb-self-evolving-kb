"""
kb-engine 统一 CLI 入口

用法：
    kb sync              # 同步知识库（增量）
    kb sync --full       # 全量重建
    kb api               # 启动 HTTP API 服务
    kb audit             # 知识库体检
    kb eval              # 检索效果评估
    kb stats             # 知识库统计
"""

from typing import Optional

import typer

from kb_engine import __version__

app = typer.Typer(
    name="kb-engine",
    help="Obsidian + ChromaDB 自进化知识检索系统",
    add_completion=False,
    no_args_is_help=True,
)


def version_callback(value: bool):
    if value:
        typer.echo(f"kb-engine v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None, "--version", "-V", callback=version_callback, is_eager=True, help="显示版本号"
    ),
):
    """Obsidian + ChromaDB 自进化知识检索系统"""
    pass


@app.command("sync")
def sync(
    full: bool = typer.Option(False, "--full", "-f", help="全量重建索引"),
):
    """同步 Obsidian Vault 到 ChromaDB"""
    import sys

    from kb_engine.sync_obsidian_to_chroma import main

    sys.argv = ["kb-sync"]
    if full:
        sys.argv.append("--full")
    main()


@app.command("api")
def api(
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="监听地址"),
    port: int = typer.Option(8300, "--port", "-p", help="监听端口"),
):
    """启动 HTTP API 服务"""
    import uvicorn

    from kb_engine.kb_api_server import app as fastapi_app

    uvicorn.run(fastapi_app, host=host, port=port)


@app.command("audit")
def audit():
    """知识库体检（frontmatter/断链/一致性）"""
    import sys

    from kb_engine.kb_audit import main

    sys.argv = ["kb-audit"]
    main()


@app.command("eval")
def evaluate():
    """检索效果评估"""
    import sys

    from kb_engine.eval_retrieval import main

    sys.argv = ["kb-eval"]
    main()


@app.command("stats")
def stats():
    """显示知识库统计信息"""
    import chromadb

    from kb_engine.config import settings

    client = chromadb.PersistentClient(path=settings.chroma_path)
    print(f"ChromaDB: {settings.chroma_path}")
    for name in [settings.collection_lsa, settings.collection_bge]:
        try:
            col = client.get_collection(name=name)
            print(f"  {name}: {col.count()} 个分块")
        except Exception:
            print(f"  {name}: (不存在)")


if __name__ == "__main__":
    app()
