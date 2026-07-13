# -*- coding: utf-8 -*-
"""
RAG 工具模块。

将 Embedding 加载、环境配置等被多个模块重复使用的逻辑集中于此，
避免代码重复。

build_rag.py / search_rag.py / search_backend.py 统一从此导入。
"""

import os
import ssl
import sys

import rag_config as cfg

# ============================================================
# 环境初始化（模块导入时自动执行一次）
# ============================================================

_initialized = False


def _setup_env():
    """配置 SSL 绕过和 HuggingFace 镜像（模块导入时自动执行一次）。"""
    global _initialized
    if _initialized:
        return
    _initialized = True

    os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFY", "1")
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
    except AttributeError:
        pass


def _configure_stdio():
    """配置 stdout/stderr 为 UTF-8（避免 Windows 终端中文乱码）。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


# ============================================================
# Embedding 模型加载（统一入口）
# ============================================================


class _APIEmbedder:
    """
    在线 API Embedder 封装。

    提供与 SentenceTransformer 兼容的 encode() 接口，
    包括 normalize_embeddings 参数支持。
    """

    def __init__(self, client, model_name):
        self.client = client
        self.model_name = model_name

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False, **kwargs):
        """
        调用在线 API 获取文本向量。

        texts: list of str — 要编码的文本列表
        normalize_embeddings: bool — 是否 L2 归一化（API 通常已归一化）
        show_progress_bar: bool — 是否显示进度条（API 请求时无实际含义）
        """
        import numpy as np

        # 在线 API 通常不支持过大批量，分批处理
        embeddings = []
        batch_size = 100  # 保守批量，避免 API 超时
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            resp = self.client.embeddings.create(
                model=self.model_name, input=batch
            )
            batch_embeddings = [d.embedding for d in resp.data]
            embeddings.extend(batch_embeddings)

        # 确保返回 numpy 数组（与 SentenceTransformer 行为一致）
        result = np.array(embeddings, dtype=np.float32)
        if normalize_embeddings:
            norms = np.linalg.norm(result, axis=1, keepdims=True)
            norms = np.maximum(norms, 1e-12)  # 避免除零
            result = result / norms
        return result


def load_embedder():
    """
    统一加载 Embedding 模型。

    根据 rag_config.py 中的 EMBED_BACKEND 配置选择后端：
        - "local"        : 加载本地 SentenceTransformer 模型（BGE 系列）
        - "openai"       : 使用 OpenAI 兼容 API
        - "siliconflow"  : 使用硅基流动 API

    返回:
        具有 encode(list_of_strings, normalize_embeddings=True) 方法的对象
        返回值为 numpy.ndarray (n_texts, dim)
    """
    backend = cfg.EMBED_BACKEND.lower()

    if backend == "local":
        from sentence_transformers import SentenceTransformer

        device = cfg.DEVICE
        if device is None:
            # 自动检测最优后端：CUDA → MPS → CPU
            import torch
            if torch.cuda.is_available():
                device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = "mps"  # Apple Silicon GPU
            else:
                device = "cpu"

        print("加载本地模型: {} (device={})...".format(cfg.LOCAL_MODEL_NAME, device))
        model = SentenceTransformer(cfg.LOCAL_MODEL_NAME, device=device)

        # 如果最终使用CPU，启用多线程优化（避免只用1个核）
        if str(model.device) == "cpu":
            import torch
            try:
                cpu_count = os.cpu_count()
            except Exception:
                cpu_count = None
            if cpu_count is None or cpu_count < 1:
                cpu_count = 4
            torch.set_num_threads(min(cpu_count, 8))

        print("  模型加载完成，运行设备: {}".format(model.device))
        return model

    if backend in ("openai", "siliconflow"):
        api_key = cfg.EMBED_API_KEY or os.environ.get("EMBED_API_KEY", "")
        if not api_key:
            raise ValueError(
                "请设置 EMBED_API_KEY 环境变量或在 rag_config.py 中配置"
            )
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=cfg.EMBED_API_BASE)
        print("使用在线 API: {} ({})".format(backend, cfg.EMBED_MODEL_NAME))
        return _APIEmbedder(client, cfg.EMBED_MODEL_NAME)

    raise ValueError("不支持的 EMBED_BACKEND: {}".format(backend))


# ============================================================
# 文本清理工具
# ============================================================


def escape_md_cell(text):
    """
    将文本转为安全的 Markdown 表格单元格内容。

    替换换行和竖线，避免破坏 Markdown 表格结构。
    统一 docx2txt 和 xlsx2txt 中原本重复的函数。
    """
    if text is None:
        return ""
    return str(text).replace("\n", " ").replace("\r", " ").replace("|", "\\|")


# ============================================================
# BM25 索引（稀疏检索，擅长精确词/编号匹配）
# ============================================================


class BM25Index:
    """
    轻量 BM25 索引，无外部依赖。

    对中文文本采用字级 + 2-gram 分词，英文按单词 + 保留数字/编号。
    与 FAISS 向量检索互补：BM25 擅长精确词命中，向量擅长语义匹配。
    """

    def __init__(self, chunk_texts, k1=None, b=None):
        import re
        from collections import defaultdict
        import math

        self._re = re
        self._defaultdict = defaultdict
        self._math = math

        self.k1 = k1 if k1 is not None else cfg.BM25_K1
        self.b = b if b is not None else cfg.BM25_B
        self.corpus = chunk_texts
        self.N = len(chunk_texts)
        self.avgdl = sum(len(d) for d in chunk_texts) / max(self.N, 1)
        self._build()

    @staticmethod
    def _tokenize(text):
        """中英文混合分词：中文单字+2-gram + 英文/数字保留。"""
        import re
        tokens = []
        for match in re.finditer(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", text.lower()):
            segment = match.group()
            if re.match(r"[\u4e00-\u9fff]", segment):
                # 中文：单字切分 + 2-gram
                tokens.extend(segment)
                for i in range(len(segment) - 1):
                    tokens.append(segment[i:i + 2])
            else:
                tokens.append(segment)
        return tokens

    def _build(self):
        self.doc_freq = self._defaultdict(int)
        self.tf = []
        self.doc_lengths = []
        for doc in self.corpus:
            tokens = self._tokenize(doc)
            self.doc_lengths.append(len(tokens))
            tf_doc = self._defaultdict(int)
            for t in tokens:
                tf_doc[t] += 1
            self.tf.append(dict(tf_doc))
            for t in set(tokens):
                self.doc_freq[t] += 1

    def search(self, query, top_k=10):
        """返回 [(idx, score), ...]，按 BM25 分数降序。"""
        if self.N == 0:
            return []
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        scores = []
        for i in range(self.N):
            score = 0.0
            dl = self.doc_lengths[i]
            tf_doc = self.tf[i]
            for t in query_tokens:
                df = self.doc_freq.get(t, 0)
                if df == 0:
                    continue
                tf = tf_doc.get(t, 0)
                idf = self._math.log((self.N - df + 0.5) / (df + 0.5) + 1.0)
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                score += idf * numerator / denominator
            scores.append((i, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def save(self, path):
        """序列化到 .pkl 文件。"""
        import pickle
        data = {
            "k1": self.k1,
            "b": self.b,
            "corpus": self.corpus,
            "N": self.N,
            "avgdl": self.avgdl,
            "doc_freq": dict(self.doc_freq),
            "tf": self.tf,
            "doc_lengths": self.doc_lengths,
        }
        with open(path, "wb") as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def load(cls, path):
        """从 .pkl 文件反序列化。"""
        import pickle
        with open(path, "rb") as f:
            data = pickle.load(f)
        obj = cls.__new__(cls)
        from collections import defaultdict
        obj._re = __import__("re")
        obj._defaultdict = defaultdict
        obj._math = __import__("math")
        obj.k1 = data["k1"]
        obj.b = data["b"]
        obj.corpus = data["corpus"]
        obj.N = data["N"]
        obj.avgdl = data["avgdl"]
        obj.doc_freq = data["doc_freq"]
        obj.tf = data["tf"]
        obj.doc_lengths = data["doc_lengths"]
        return obj


# ============================================================
# RRF 融合（Reciprocal Rank Fusion）
# ============================================================


def rrf_fusion(ranked_lists, k=None):
    """
    倒数排名融合多路检索结果。

    ranked_lists: list of [(index, score), ...]，每路结果按分数降序
    k: RRF 平滑常数（默认 cfg.RRF_K）

    返回: [(index, fused_score), ...] 按融合分数降序
    """
    if k is None:
        k = cfg.RRF_K

    fused = {}
    for lst in ranked_lists:
        for rank, (idx, _score) in enumerate(lst, start=1):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank)

    return sorted(fused.items(), key=lambda x: x[1], reverse=True)


# 导入时自动执行环境初始化
_setup_env()
