# -*- coding: utf-8 -*-
"""
RAG 索引与检索 — 全局配置

所有可调参数集中在此，修改后 build_rag.py 和 search_rag.py 自动生效。
"""

from pathlib import Path

# ============================================================
# 路径配置
# ============================================================

# TXT 归档目录（archive.py 的输出目录）
# 可以是绝对路径或相对路径
# 推荐通过命令行传入: python build_rag.py ./TXT归档
TXT_ARCHIVE_DIR = Path(__file__).parent / "TXT归档"

# FAISS 向量索引子目录名（会创建在 TXT 归档目录内）
# 例如: D:\公司文档_TXT归档\_faiss/
FAISS_SUBDIR = "_faiss"

# 文档库检索提示词文件路径（作为 LLM system prompt）
PROMPT_FILE = Path(__file__).parent / "docs" / "文档库检索提示词.md"

# ============================================================
# Embedding 模型配置
# ============================================================

# 后端选择: "local" | "openai" | "siliconflow"
EMBED_BACKEND = "local"

# ---- 本地模型配置 ----
# BGE-large-zh-v1.5: 中文语义搜索最优开源模型之一
# 首次运行自动从 HuggingFace 下载（~1.3GB），之后缓存
LOCAL_MODEL_NAME = "BAAI/bge-large-zh-v1.5"

# 运行设备: "cuda" | "cpu" | None（None=自动检测）
# - "cuda": 强制 GPU（需要 NVIDIA 显卡 + CUDA）
# - "cpu":  强制 CPU
# - None:   自动检测，有 GPU 就用 GPU
DEVICE = None  # 推荐 None，自动适配

# ---- 在线 API 配置（当 EMBED_BACKEND != "local" 时生效） ----
# 硅基流动 (siliconflow.cn): 免费额度，中文友好
# OpenAI: 付费，全球可用
EMBED_API_KEY = ""          # 在此填入 API Key，或设置环境变量 EMBED_API_KEY
EMBED_API_BASE = "https://api.siliconflow.cn/v1"  # 硅基流动
EMBED_MODEL_NAME = "BAAI/bge-large-zh-v1.5"       # 硅基流动上对应的模型名

# ============================================================
# 文本切分配置
# ============================================================

# 每块最大字符数
CHUNK_MAX_CHARS = 800

# 每块最小字符数（小于此值的块会与下一块合并）
CHUNK_MIN_CHARS = 200

# 块间重叠字符数（避免语义在边界断裂）
CHUNK_OVERLAP = 100

# ============================================================
# 文档转换配置
# ============================================================

# XLSX 最大读取行数（0 = 不限制，读取全部行）
# 对于数十万行的超大表格建议设置上限（如 50000），避免内存溢出
XLSX_MAX_ROWS = 0

# ============================================================
# LLM 配置（search_rag.py --llm 模式使用）
# ============================================================

# 是否启用 LLM 回答模式
LLM_ENABLED = False

# LLM API 配置
LLM_API_KEY = ""            # 在此填入，或设环境变量 LLM_API_KEY
LLM_API_BASE = "https://api.deepseek.com/v1"  # DeepSeek 兼容 OpenAI 接口
LLM_MODEL_NAME = "deepseek-chat"              # 也可用 gpt-4o-mini / qwen-turbo 等

# 检索时返回的文本块数量
SEARCH_TOP_K = 5

# ============================================================
# 混合检索配置（BM25 + 向量）
# ============================================================

# 是否启用混合检索
HYBRID_ENABLED = True

# 每路检索的召回倍数（每路取 top_k × RECALL_K，融合后截取 top_k）
HYBRID_RECALL_K = 3

# RRF（倒数排名融合）平滑常数，经典值 60
RRF_K = 60

# BM25 参数
BM25_K1 = 1.2    # 词频饱和度（经典值 1.2-2.0）
BM25_B = 0.75    # 长度归一化（经典值 0.75）

# 每次发给 LLM 的最大上下文字符数
LLM_MAX_CONTEXT_CHARS = 8000


def validate():
    """
    校验配置参数合法性，发现矛盾配置立即报错。
    在 build_rag.py / search_rag.py / search_backend.py 首次导入时自动调用。
    """
    errors = []

    if CHUNK_MIN_CHARS > CHUNK_MAX_CHARS:
        errors.append(
            "CHUNK_MIN_CHARS ({}) 不能大于 CHUNK_MAX_CHARS ({})".format(
                CHUNK_MIN_CHARS, CHUNK_MAX_CHARS
            )
        )
    if CHUNK_OVERLAP >= CHUNK_MAX_CHARS:
        errors.append(
            "CHUNK_OVERLAP ({}) 应小于 CHUNK_MAX_CHARS ({})".format(
                CHUNK_OVERLAP, CHUNK_MAX_CHARS
            )
        )
    if CHUNK_OVERLAP < 0:
        errors.append("CHUNK_OVERLAP 不能为负数")
    if CHUNK_MAX_CHARS < 100:
        errors.append("CHUNK_MAX_CHARS 过小（< 100），建议 >= 300")
    if CHUNK_MIN_CHARS < 50:
        errors.append("CHUNK_MIN_CHARS 过小（< 50），建议 >= 100")

    valid_backends = ("local", "openai", "siliconflow")
    if EMBED_BACKEND.lower() not in valid_backends:
        errors.append(
            "不支持的 EMBED_BACKEND: '{}'，可选值: {}".format(
                EMBED_BACKEND, ", ".join(valid_backends)
            )
        )

    if SEARCH_TOP_K < 1:
        errors.append("SEARCH_TOP_K 必须 >= 1")

    if errors:
        raise ValueError(
            "rag_config 配置错误:\n" + "\n".join("  - {}".format(e) for e in errors)
        )


# 导入时自动校验配置
validate()