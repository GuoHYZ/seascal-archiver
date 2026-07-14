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

# 兼容直接运行（python search/build_rag.py）和从上级导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rag_config as cfg
from rag_utils import load_embedder


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


def _split_large_paragraphs(content, max_chars):
    """将超长段落按行边界强制切分，避免产生巨型 chunk。

    第一阶段：按 \\n\\n（段落边界）切分。
    第二阶段：对仍超长的段落按 \\n（Markdown 表格行边界）切分。
    """
    paragraphs = content.split("\n\n")
    result = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            result.append(para)
        else:
            # 强制按行切分（对 Markdown 表格友好）
            lines = para.split("\n")
            sub = ""
            for line in lines:
                stripped = line.rstrip()
                if len(sub) + len(stripped) + 1 <= max_chars:
                    sub += ("\n" + stripped) if sub else stripped
                else:
                    if sub:
                        result.append(sub)
                    sub = stripped
            if sub:
                result.append(sub)
    return result


def _build_embedding_text(chunk):
    """构建用于向量化的文本（直接使用 chunk 正文，不加前缀以保持语义一致）。"""
    return chunk["text"]


def _file_sha256(file_path):
    """计算文件前 64KB 的 SHA256 哈希。"""
    try:
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            sha.update(f.read(65536))
        return sha.hexdigest()
    except (IOError, OSError):
        return None


# ============================================================
# 文本切分（修复版 — 消除数据丢失 & 超大 chunk 问题）
# ============================================================


def _commit_chunk(chunks, text):
    """将 text 存入 chunks（去重纯空白）。"""
    if text.strip():
        chunks.append(text)


def _split_to_chunks(paragraphs, max_chars, min_chars, overlap):
    """
    将段落列表组装为不超过 max_chars 的文本块。

    核心保证:
    1. 每个段落都被处理，不丢数据
    2. 任何 chunk 都不超过 max_chars
    3. overlap 在 chunk 边界保留上下文
    """
    chunks = []
    current = ""

    for para in paragraphs:
        # 情况 1: para 能放入 current
        if len(current) + len(para) + 2 <= max_chars:
            current += ("\n\n" + para) if current else para
            continue

        # 情况 2: current 已足够大 → 存入 chunk，para 作为新块
        if current and len(current) >= min_chars:
            _commit_chunk(chunks, current)
            # 设置重叠：保留尾部字符
            if overlap > 0 and len(current) > overlap:
                current = current[-overlap:]
            else:
                current = ""
            # now handle para — 可能超长
            if len(para) >= max_chars:
                _commit_chunk(chunks, para[:max_chars])
                if overlap > 0 and len(para) > overlap:
                    current = para[max(0, len(para) - overlap):]
                else:
                    current = ""
            else:
                current = para
            continue

        # 情况 3: current 太小，合并后存入，或者分别处理
        if current:
            merged = current + "\n\n" + para
            if len(merged) <= max_chars:
                _commit_chunk(chunks, merged)
                current = ""
            else:
                # 合并后仍然超限 → 分别存入
                _commit_chunk(chunks, current)
                if len(para) >= max_chars:
                    _commit_chunk(chunks, para[:max_chars])
                    if overlap > 0 and len(para) > overlap:
                        current = para[max(0, len(para) - overlap):]
                    else:
                        current = ""
                else:
                    current = para
            continue

        # 情况 4: 无 current，para 本身 ≥ max_chars → 硬切
        if len(para) >= max_chars:
            _commit_chunk(chunks, para[:max_chars])
            if overlap > 0 and len(para) > overlap:
                current = para[max(0, len(para) - overlap):]
            else:
                current = ""
        else:
            current = para

    # 处理剩余内容
    if current:
        if len(current) >= min_chars or not chunks:
            _commit_chunk(chunks, current)
        else:
            chunks[-1] += "\n\n" + current

    return chunks


def _split_text(content, source_rel, max_chars, min_chars, overlap):
    """将文本按 max_chars 切分为重叠块。"""
    paragraphs = _split_large_paragraphs(content, max_chars)
    chunk_texts = _split_to_chunks(paragraphs, max_chars, min_chars, overlap)

    result = []
    char_offset = 0
    for idx, chunk in enumerate(chunk_texts):
        result.append({
            "text": chunk,
            "metadata": {
                "source": source_rel,
                "chunk_index": idx,
                "char_start": char_offset,
                "total_chunks": len(chunk_texts),
            },
        })
        char_offset += len(chunk) + 2
    return result


# ============================================================
# 主流程
# ============================================================


def build_index(txt_dir=None, force=False):
    import numpy as np
    import faiss

    if txt_dir is None:
        txt_dir = cfg.TXT_ARCHIVE_DIR
    else:
        txt_dir = Path(txt_dir)
    txt_dir = Path(txt_dir)

    faiss_dir = Path(txt_dir).resolve() / cfg.FAISS_SUBDIR
    meta_path = faiss_dir / "meta.json"

    # ---- 读取 TXT ----
    print("读取 TXT 文件: {}".format(txt_dir))
    files = _read_txt_files(txt_dir)
    if not files:
        return
    print("  找到 {} 个文件".format(len(files)))

    # ---- 增量检测 ----
    prev_data = {}
    changed_files = []
    unchanged_files = []
    if force:
        changed_files = files
        print("  --force：强制全量重建")
    elif meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                prev_data = json.load(f)
            prev_files_meta = prev_data.get("files_meta", {})
            for rel, content in files:
                sha = _file_sha256(txt_dir / rel)
                prev = prev_files_meta.get(rel, {})
                if sha and prev.get("sha256") == sha:
                    unchanged_files.append((rel, content))
                else:
                    changed_files.append((rel, content))
            removed = set(prev_files_meta.keys()) - set(rel for rel, _ in files)
            if removed:
                print("  检测到 {} 个文件已移除".format(len(removed)))
            if not changed_files and not removed:
                print("  所有文件未变更，索引已是最新。")
                return
            print("  需重建: {} 个,  未变更: {} 个".format(
                len(changed_files), len(unchanged_files)
            ))
        except (json.JSONDecodeError, KeyError):
            print("  [警告] 索引元数据损坏，执行全量重建")
            changed_files = files
    else:
        changed_files = files

    # 合并：未变更文件保留原始 chunk 结构（从旧 meta 恢复）
    prev_records = prev_data.get("records", []) if prev_data else []
    prev_records_by_source = {}
    for rec in prev_records:
        src = rec.get("metadata", {}).get("source", "")
        prev_records_by_source.setdefault(src, []).append(rec)

    # ---- 切分（仅变更文件） ----
    print("\n切分文本块 ...")
    all_chunks = []

    # 未变更文件的旧 chunk
    for rel, _content in unchanged_files:
        old_chunks = prev_records_by_source.get(rel, [])
        all_chunks.extend(old_chunks)

    # 变更文件重新切分
    for rel, content in changed_files:
        try:
            all_chunks.extend(
                _split_text(content, rel, cfg.CHUNK_MAX_CHARS, cfg.CHUNK_MIN_CHARS, cfg.CHUNK_OVERLAP)
            )
        except Exception as e:
            print("  [跳过] {} — 切分失败: {}".format(rel, e))

    print("  共生成 {} 个文本块（复用 {} 个，新切 {} 个文件）".format(
        len(all_chunks), len(unchanged_files), len(changed_files)
    ))
    if not all_chunks:
        print("[错误] 未生成任何文本块")
        return

    # ---- 加载 Embedding ----
    print("\n加载 Embedding 模型...")
    try:
        model = load_embedder()
    except Exception as e:
        print("[错误] 模型加载失败: {}".format(e))
        return

    # ---- 向量化（增量：复用旧 embedding，仅编码新 chunk） ----
    print("\n向量化 {} 个文本块...".format(len(all_chunks)))
    old_index_path = faiss_dir / "index.faiss"
    unchanged_count = sum(1 for c in all_chunks if "id" in c)  # 带旧 id 的 chunk 是复用的

    if not force and old_index_path.exists() and unchanged_count > 0:
        # 加载旧 FAISS 索引，提取未变更 chunk 的旧向量
        print("  从旧索引复用 {} 个未变更 chunk 的向量...".format(unchanged_count))
        old_index = faiss.read_index(str(old_index_path))
        old_ids = np.array(
            [c["id"] for c in all_chunks[:unchanged_count] if "id" in c],
            dtype="int64",
        )
        old_embeddings = old_index.reconstruct_batch(old_ids)

        # 仅编码新增/变更的 chunk
        new_chunks = all_chunks[unchanged_count:]
        if new_chunks:
            new_texts = [_build_embedding_text(c) for c in new_chunks]
            print("  编码 {} 个新增/变更 chunk...".format(len(new_chunks)))
            new_embeddings = np.array(
                model.encode(new_texts, normalize_embeddings=True, show_progress_bar=True)
            ).astype("float32")
            embeddings = np.vstack([old_embeddings, new_embeddings])
        else:
            embeddings = old_embeddings
        print("  总计 {} 条向量（复用 {}，新编码 {}）".format(
            len(embeddings), unchanged_count, len(new_chunks) if new_chunks else 0
        ))
    else:
        # 全量编码
        texts = [_build_embedding_text(c) for c in all_chunks]
        embeddings = np.array(
            model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
        ).astype("float32")

    # ---- 构建 FAISS 索引 ----
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    print("  FAISS 索引: {} 条, 维度 {}".format(index.ntotal, dim))

    # ---- 存储 ----
    faiss_dir.mkdir(parents=True, exist_ok=True)

    faiss.write_index(index, str(faiss_dir / "index.faiss"))

    # 存元数据 + 文本（JSON）
    records = []
    files_meta = {}
    for i, c in enumerate(all_chunks):
        records.append({
            "id": i,
            "text": c["text"],
            "metadata": c["metadata"],
        })
    # 记录每个文件的 SHA256
    for rel, content in files:
        sha = _file_sha256(txt_dir / rel)
        if sha:
            files_meta[rel] = {"sha256": sha, "char_count": len(content)}

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "created": datetime.now().isoformat(),
            "total": len(records),
            "files_meta": files_meta,
            "records": records,
        }, f, ensure_ascii=False, indent=2)

    print("  元数据已存储: {}".format(meta_path))

    # ---- 构建 BM25 索引 ----
    print("\n构建 BM25 索引...")
    from rag_utils import BM25Index
    chunk_texts = [_build_embedding_text(c) for c in all_chunks]
    bm25 = BM25Index(chunk_texts, k1=cfg.BM25_K1, b=cfg.BM25_B)
    bm25_path = faiss_dir / "bm25.pkl"
    bm25.save(bm25_path)
    bm25_size = bm25_path.stat().st_size / 1024 / 1024
    print("  BM25 索引: {} 条, {:.1f} MB".format(bm25.N, bm25_size))

    print("\n索引构建完成")
    print("  文本块总数: {}".format(len(records)))
    print("  FAISS 目录: {}".format(faiss_dir))


if __name__ == "__main__":
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    txt_dir = positional[0] if positional else None
    build_index(txt_dir=txt_dir, force=force)