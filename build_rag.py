# -*- coding: utf-8 -*-
"""
RAG 索引构建工具（FAISS 版）

将 TXT 归档目录中的所有文档切片、向量化，存入 FAISS 索引，
后续可用 search_rag.py 进行语义检索。

用法:
    python build_rag.py [TXT归档目录]
"""

import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime

import os as _os
_os.environ["HF_HUB_DISABLE_SSL_VERIFY"] = "1"
_os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
import ssl as _ssl
_ssl._create_default_https_context = _ssl._create_unverified_context

import rag_config as cfg


def _read_txt_files(txt_dir):
    txt_dir = Path(txt_dir)
    if not txt_dir.is_dir():
        print("[错误] TXT 归档目录不存在: {}".format(txt_dir))
        return []
    files = sorted(txt_dir.rglob("*.txt"))
    files = [f for f in files if not f.name.startswith("_")]
    if not files:
        print("[警告] 未找到任何 TXT 文件（已自动排除 _索引.txt 等内部文件）")
        return []
    result = []
    for fp in files:
        try:
            content = fp.read_text(encoding="utf-8-sig", errors="replace")
            rel = str(fp.relative_to(txt_dir))
            result.append((rel, content))
        except Exception as e:
            print("[跳过] {} — 读取失败: {}".format(fp.name, e))
    return result


def _split_text(content, source_rel, max_chars, min_chars, overlap):
    paragraphs = content.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= max_chars:
            current += ("\n\n" + para) if current else para
        else:
            if current and len(current) >= min_chars:
                chunks.append(current)
                current = current[-overlap:] if overlap > 0 and len(current) > overlap else ""
            else:
                chunks.append(current + "\n\n" + para if current else para)
                current = ""
    if current:
        if len(current) >= min_chars or not chunks:
            chunks.append(current)
        else:
            chunks[-1] += "\n\n" + current
    result = []
    char_offset = 0
    for idx, chunk in enumerate(chunks):
        result.append({
            "text": chunk,
            "metadata": {
                "source": source_rel,
                "chunk_index": idx,
                "char_start": char_offset,
                "total_chunks": len(chunks),
            },
        })
        char_offset += len(chunk) + 2
    return result


def _load_embedder():
    backend = cfg.EMBED_BACKEND.lower()
    if backend == "local":
        import ssl
        ssl._create_default_https_context = ssl._create_unverified_context
        from sentence_transformers import SentenceTransformer
        device = cfg.DEVICE
        print("加载本地模型: {} ...".format(cfg.LOCAL_MODEL_NAME))
        model = SentenceTransformer(cfg.LOCAL_MODEL_NAME, device=device) if device else SentenceTransformer(cfg.LOCAL_MODEL_NAME)
        print("  模型加载完成，运行设备: {}".format(model.device))
        return model
    elif backend in ("openai", "siliconflow"):
        import os
        api_key = cfg.EMBED_API_KEY or os.environ.get("EMBED_API_KEY", "")
        if not api_key:
            raise ValueError("请设置 EMBED_API_KEY 或环境变量 EMBED_API_KEY")
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=cfg.EMBED_API_BASE)
        class APIEmbedder:
            def __init__(self, c, m): self.client, self.model = c, m
            def encode(self, texts, **kwargs):
                resp = self.client.embeddings.create(model=self.model, input=texts)
                return [d.embedding for d in resp.data]
        print("使用在线 API: {} ({})".format(backend, cfg.EMBED_MODEL_NAME))
        return APIEmbedder(client, cfg.EMBED_MODEL_NAME)
    else:
        raise ValueError("不支持的 EMBED_BACKEND: {}".format(backend))


def build_index(txt_dir=None):
    import numpy as np
    import faiss

    if txt_dir is None:
        txt_dir = cfg.TXT_ARCHIVE_DIR
    else:
        txt_dir = Path(txt_dir)
    txt_dir = Path(txt_dir)

    # ---- 读取 TXT ----
    print("读取 TXT 文件: {}".format(txt_dir))
    files = _read_txt_files(txt_dir)
    if not files:
        return
    print("  找到 {} 个文件".format(len(files)))

    # ---- 切分 ----
    print("\n切分文本块 ...")
    all_chunks, failed_files = [], 0
    for rel, content in files:
        try:
            all_chunks.extend(_split_text(content, rel, cfg.CHUNK_MAX_CHARS, cfg.CHUNK_MIN_CHARS, cfg.CHUNK_OVERLAP))
        except Exception as e:
            print("  [跳过] {} — 切分失败: {}".format(rel, e))
            failed_files += 1
    print("  共生成 {} 个文本块".format(len(all_chunks)))
    if not all_chunks:
        print("[错误] 未生成任何文本块")
        return

    # ---- 加载 Embedding ----
    print("\n加载 Embedding 模型...")
    try:
        model = _load_embedder()
    except Exception as e:
        print("[错误] 模型加载失败: {}".format(e))
        return

    # ---- 向量化 ----
    print("\n向量化 {} 个文本块...".format(len(all_chunks)))
    texts = [c["text"] for c in all_chunks]
    metadatas = [c["metadata"] for c in all_chunks]
    embeddings = np.array(model.encode(texts, normalize_embeddings=True, show_progress_bar=True)).astype("float32")

    # ---- 构建 FAISS 索引 ----
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)  # 内积 = 余弦相似度（归一化后）
    index.add(embeddings)
    print("  FAISS 索引: {} 条, 维度 {}".format(index.ntotal, dim))

    # ---- 存储 ----
    faiss_dir = Path(txt_dir).resolve() / cfg.FAISS_SUBDIR
    faiss_dir.mkdir(parents=True, exist_ok=True)

    # 存 FAISS 索引
    faiss.write_index(index, str(faiss_dir / "index.faiss"))

    # 存元数据 + 文本（JSON）
    records = []
    for i, c in enumerate(all_chunks):
        records.append({
            "id": i,
            "text": c["text"],
            "metadata": c["metadata"],
        })
    meta_path = faiss_dir / "meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "created": datetime.now().isoformat(),
            "total": len(records),
            "records": records,
        }, f, ensure_ascii=False, indent=2)

    print("  元数据已存储: {}".format(meta_path))
    print("\n索引构建完成")
    print("  文本块总数: {}".format(len(records)))
    print("  FAISS 目录: {}".format(faiss_dir))


if __name__ == "__main__":
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    txt_dir = positional[0] if positional else None
    build_index(txt_dir=txt_dir)