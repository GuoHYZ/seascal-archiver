# -*- coding: utf-8 -*-
"""
RAG 语义检索工具（FAISS 版，支持 BM25 混合检索）

在已构建的向量索引中搜索相关内容，支持三种模式:
- 静默模式: 只返回相关文本块及来源
- 混合模式（默认）: BM25 + 向量 RRF 融合检索
- LLM 模式: 检索 + 调用在线 LLM 生成完整回答

用法:
    python search_rag.py "你的问题" --db <TXT归档目录> [--llm]
"""

import sys
import json
import os
from pathlib import Path

import rag_config as cfg
from rag_utils import load_embedder, BM25Index, rrf_fusion


def _load_faiss_and_meta(faiss_dir):
    import faiss

    idx_path = Path(faiss_dir) / "index.faiss"
    meta_path = Path(faiss_dir) / "meta.json"
    if not idx_path.exists():
        print("[错误] FAISS 索引不存在: {}".format(idx_path))
        print("       请先运行: python build_rag.py")
        return None, None
    index = faiss.read_index(str(idx_path))
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return index, meta["records"]


def _load_bm25(faiss_dir):
    bm25_path = Path(faiss_dir) / "bm25.pkl"
    if not bm25_path.exists():
        return None
    try:
        return BM25Index.load(bm25_path)
    except Exception:
        return None


def search(query, top_k=None, db_dir=None):
    if top_k is None:
        top_k = cfg.SEARCH_TOP_K

    if db_dir is None:
        faiss_dir = Path(cfg.TXT_ARCHIVE_DIR).resolve() / cfg.FAISS_SUBDIR
    else:
        faiss_dir = Path(db_dir).resolve() / cfg.FAISS_SUBDIR

    if not faiss_dir.exists():
        print("[错误] 向量索引不存在: {}".format(faiss_dir))
        print("       请先运行: python build_rag.py")
        return []

    index, records = _load_faiss_and_meta(faiss_dir)
    if index is None or records is None:
        return []

    try:
        model = load_embedder()
    except Exception as e:
        print("[错误] Embedding 模型加载失败: {}".format(e))
        return []

    import numpy as np

    # ---- 向量检索 ----
    query_vec = np.array(
        [model.encode([query], normalize_embeddings=True)[0]]
    ).astype("float32")

    # ---- 混合检索（BM25 + 向量 RRF 融合） ----
    if cfg.HYBRID_ENABLED:
        bm25 = _load_bm25(faiss_dir)
        if bm25 is not None:
            recall_k = max(int(top_k) * cfg.HYBRID_RECALL_K, top_k + 5)

            # 两路召回
            bm25_pairs = bm25.search(query, top_k=recall_k)
            candidate_k = max(recall_k * 4, 20)
            scores, indices = index.search(query_vec, candidate_k)
            vector_pairs = []
            for i, s in zip(indices[0], scores[0]):
                if 0 <= i < len(records):
                    vector_pairs.append((int(i), float(s)))
            vector_pairs = vector_pairs[:recall_k]

            # RRF 融合
            fused = rrf_fusion([bm25_pairs, vector_pairs], k=cfg.RRF_K)
            top_indices = fused[:int(top_k)]

            formatted = []
            for idx, fused_score in top_indices:
                rec = records[idx]
                meta = rec["metadata"]
                bm25_score = next((s for i, s in bm25_pairs if i == idx), 0.0)
                vector_score = next((s for i, s in vector_pairs if i == idx), 0.0)
                formatted.append({
                    "text": rec["text"],
                    "source": meta.get("source", "未知"),
                    "chunk_index": meta.get("chunk_index", 0),
                    "score": round(fused_score, 4),
                    "bm25_score": round(bm25_score, 4),
                    "vector_score": round(vector_score, 4),
                })
            return formatted

    # ---- 纯向量检索（回退） ----
    scores, indices = index.search(query_vec, top_k)

    formatted = []
    for idx, score in zip(indices[0], scores[0]):
        if idx < 0 or idx >= len(records):
            continue
        rec = records[idx]
        meta = rec["metadata"]
        formatted.append({
            "text": rec["text"],
            "source": meta.get("source", "未知"),
            "chunk_index": meta.get("chunk_index", 0),
            "score": round(float(score), 4),
        })
    return formatted


def _load_system_prompt():
    prompt_file = cfg.PROMPT_FILE
    if not prompt_file.exists():
        return "你是一个办公文档库的专业检索助手。请基于提供的文档内容回答问题，每条数据必须标注来源。"
    content = prompt_file.read_text(encoding="utf-8")
    start_marker = "## 提示词正文"
    idx = content.find(start_marker)
    if idx >= 0:
        content = content[idx + len(start_marker):]
    return "\n".join(content.strip().split("\n"))


def _build_context(chunks, max_chars):
    parts = []
    total = 0
    for c in chunks:
        header = "\n--- [来源: {} (块{})] ---\n".format(c["source"], c["chunk_index"])
        block = header + c["text"]
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


def answer_with_llm(query, top_k=None, db_dir=None):
    chunks = search(query, top_k, db_dir)
    if not chunks:
        return "未在文档库中找到相关信息。"

    api_key = cfg.LLM_API_KEY or os.environ.get("LLM_API_KEY", "")
    if not api_key:
        return (
            "需要设置 LLM_API_KEY 才能使用回答模式。\n\n"
            "以下是在文档库中找到的相关内容:\n\n{}".format(
                _build_context(chunks, cfg.LLM_MAX_CONTEXT_CHARS)
            )
        )

    context = _build_context(chunks, cfg.LLM_MAX_CONTEXT_CHARS)
    system_prompt = _load_system_prompt()

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=cfg.LLM_API_BASE)
        user_message = (
            "以下是文档库中与用户问题相关的文本内容:\n\n"
            "=== 文档内容开始 ===\n{}\n=== 文档内容结束 ===\n\n"
            "用户问题: {}".format(context, query)
        )
        response = client.chat.completions.create(
            model=cfg.LLM_MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        return response.choices[0].message.content
    except Exception as e:
        return "LLM 调用失败: {}\n\n以下是检索到的相关内容:\n\n{}".format(
            e, _build_context(chunks, cfg.LLM_MAX_CONTEXT_CHARS)
        )


def main():
    args = sys.argv[1:]
    use_llm = "--llm" in args
    db_dir = None

    for i, a in enumerate(args):
        if a == "--db" and i + 1 < len(args):
            db_dir = args[i + 1]
            break

    skip_next = False
    query_parts = []
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a.startswith("--"):
            if a == "--db":
                skip_next = True
            continue
        query_parts.append(a)
    query = " ".join(query_parts).strip()

    if not query:
        print("用法: python search_rag.py <问题> --db <TXT归档目录> [--llm]")
        print()
        print("示例:")
        print('  python search_rag.py "铁塔维护费用是多少" --db ./TXT归档')
        print('  python search_rag.py "铁塔维护费用是多少" --db D:\\公司文档_TXT归档 --llm')
        sys.exit(1)

    if use_llm:
        print("检索中...")
        answer = answer_with_llm(query, db_dir=db_dir)
        print("\n" + "=" * 60)
        print(answer)
        print("=" * 60)
    else:
        chunks = search(query, db_dir=db_dir)
        if not chunks:
            print("未找到相关内容。")
            return

        mode_label = "混合检索" if cfg.HYBRID_ENABLED else "向量检索"
        print("检索结果 ({}，{} 条)：\n".format(mode_label, len(chunks)))
        for i, c in enumerate(chunks, 1):
            print("─" * 50)
            extra = ""
            if "bm25_score" in c:
                extra = "  BM25: {:.4f}  向量: {:.4f}".format(
                    c["bm25_score"], c["vector_score"]
                )
            print("#{}  来源: {} (块{})  分数: {:.4f}{}".format(
                i, c["source"], c["chunk_index"], c["score"], extra
            ))
            print("─" * 50)
            text = c["text"]
            if len(text) > 500:
                text = text[:500] + "\n... [截断，共 {} 字符]".format(len(c["text"]))
            print(text)
            print()


if __name__ == "__main__":
    main()