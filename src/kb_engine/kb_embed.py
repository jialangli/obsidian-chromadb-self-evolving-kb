"""
Knowledge 知识库 神经语义向量编码器 (bge-small-zh-v1.5)
====================================================
用真实中文语义向量替换/补充 LSA，检索泛化能力更强。

要点：
  - 模型走 huggingface 本地缓存；设 local_files_only=True，离线确定性、启动更快
  - bge 检索最佳实践：查询侧加指令前缀，文档侧不加
  - 输出 L2 归一化，余弦相似度 = 点积
"""

import os

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# bge-zh 系列检索指令前缀（仅用于 query，显著提升召回）
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
MODEL_NAME = "BAAI/bge-small-zh-v1.5"


class BgeEmbedder:
    """惰性加载 sentence-transformers 模型；供 sync 与检索侧共用。"""

    def __init__(self, model_name: str = MODEL_NAME, local_files_only: bool = True):
        self.model_name = model_name
        self._model = None
        self._local_files_only = local_files_only
        self._dim = None

    def _load(self):
        if self._model is None:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            from sentence_transformers import SentenceTransformer

            # 关键：必须显式传 local_files_only=True！
            # 新版 sentence-transformers 会用构造函数参数覆盖 HF_HUB_OFFLINE 环境变量，
            # 不传的话即使 offline 模式也会去联网 HEAD 检查 adapter_config.json 等文件。
            self._model = SentenceTransformer(
                self.model_name,
                device="cpu",
                local_files_only=self._local_files_only,
            )
            try:
                self._dim = self._model.get_embedding_dimension()
            except AttributeError:
                self._dim = self._model.get_sentence_embedding_dimension()
        return self._model

    @property
    def dim(self) -> int:
        self._load()
        return self._dim

    def encode_docs(self, texts, batch_size: int = 64):
        import numpy as np

        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        m = self._load()
        return m.encode(
            list(texts),
            batch_size=batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).astype("float32")

    def encode_query(self, query: str):
        m = self._load()
        vec = m.encode(
            [BGE_QUERY_PREFIX + query],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vec[0].astype("float32")


def available() -> bool:
    """纯本地探测：模型关键文件是否已在 HF 缓存中（不联网、不触发下载）"""
    try:
        from huggingface_hub import try_to_load_from_cache

        required = [
            "config.json",
            "model.safetensors",
            "tokenizer.json",
            "vocab.txt",
            "modules.json",
            "config_sentence_transformers.json",
            "sentence_bert_config.json",
            "1_Pooling/config.json",
        ]
        for f in required:
            r = try_to_load_from_cache(MODEL_NAME, f, revision="main")
            if not isinstance(r, str) or not os.path.exists(r):
                return False
        return True
    except Exception:
        return False


if __name__ == "__main__":
    print("model cached locally:", available())
    e = BgeEmbedder()
    print("dim:", e.dim)
    docs = ["火星救援得分体系包括任务完成度、操作规范性、用时效率。"]

    dv = e.encode_docs(docs)
    qv = e.encode_query("火星救援的分数怎么算")
    print("cos:", round(float(qv @ dv[0]), 3))
