# -*- coding: utf-8 -*-
"""
RAG 语义检索工具（CLI 包装，复用 SearchBackend）

在已构建的向量索引中搜索相关内容，支持两种模式:
- 静默模式: 只返回相关文本块及来源
- LLM 模式: 检索 + 调用在线 LLM 生成完整回答

用法:
    python search_rag.py "你的问题" --db <TXT归档目录> [--llm]
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rag_config as cfg
from search_backend import SearchBackend


def search(query, top_k=None, db_dir=None):
    """
    检索接口（复用 SearchBackend，与 search_backend.py 共享同一逻辑）。

    返回: list of dict，字段见 SearchBackend.search() 的返回值。
    """
    backend = SearchBackend(db_dir=db_dir)
    try:
        backend.load()
        return backend.search(query, top_k=top_k)
    except Exception as e:
        print("[错误] 检索失败: {}".format(e))
        return []


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
        header = "\n--- [来源: {} (块{})] ---\n".format(c.get("source", "未知"), c.get("chunk_index", 0))
        block = header + c.get("text", "")
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
                    c.get("bm25_score", 0), c.get("vector_score", 0)
                )
            print("#{}  来源: {} (块{})  分数: {:.4f}{}".format(
                i, c.get("source", "未知"), c.get("chunk_index", 0), c.get("score", 0), extra
            ))
            print("─" * 50)
            text = c.get("text", "")
            if len(text) > 500:
                text = text[:500] + "\n... [截断，共 {} 字符]".format(len(text))
            print(text)
            print()


if __name__ == "__main__":
    main()