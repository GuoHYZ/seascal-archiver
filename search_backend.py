# -*- coding: utf-8 -*-
"""
本地 RAG 查询后端。

用途：
1. 启动时只加载一次 embedding 模型和 FAISS 索引
2. 通过本地 HTTP 接口提供 UTF-8 JSON 检索结果
3. 适合作为 AionUI 等应用的外部知识库查询后端

用法：
    python search_backend.py --db ./Yaoke3_Archives
    python search_backend.py --host 127.0.0.1 --port 8765 --db ./Yaoke3_Archives

接口：
    GET  /health
    GET  /search?q=查询词&top_k=5
    POST /search    {"query": "...", "top_k": 5}
"""

import json
import os
import re
import ssl as _ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ["HF_HUB_DISABLE_SSL_VERIFY"] = "1"
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
_ssl._create_default_https_context = _ssl._create_unverified_context

import rag_config as cfg


def _configure_stdio():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _normalize_query(query):
    return re.sub(r"\s+", " ", query).strip()


def _extract_query_terms(query):
    normalized = _normalize_query(query)
    if not normalized:
        return []
    terms = [term.strip() for term in re.split(r"\s+", normalized) if term.strip()]
    return list(dict.fromkeys(terms))


def _score_keyword_hits(text, source, terms):
    score = 0.0
    source_lower = source.lower()
    text_lower = text.lower()
    for term in terms:
        lowered = term.lower()
        if lowered in source_lower:
            score += 0.12
        if lowered in text_lower:
            score += 0.03
    return score


class SearchBackend:
    def __init__(self, db_dir=None):
        self.db_dir = Path(db_dir) if db_dir else Path(cfg.TXT_ARCHIVE_DIR)
        self.faiss_dir = self.db_dir.resolve() / cfg.FAISS_SUBDIR
        self.model = None
        self.index = None
        self.records = None

    def load(self):
        if not self.faiss_dir.exists():
            raise FileNotFoundError(
                "向量索引目录不存在: {}。请先运行 python build_rag.py".format(self.faiss_dir)
            )

        self.index, self.records = self._load_faiss_and_meta()
        self.model = self._load_embedder()

    def _load_embedder(self):
        backend = cfg.EMBED_BACKEND.lower()
        if backend == "local":
            import ssl
            from sentence_transformers import SentenceTransformer

            ssl._create_default_https_context = ssl._create_unverified_context
            device = cfg.DEVICE
            return (
                SentenceTransformer(cfg.LOCAL_MODEL_NAME, device=device)
                if device
                else SentenceTransformer(cfg.LOCAL_MODEL_NAME)
            )

        if backend in ("openai", "siliconflow"):
            api_key = cfg.EMBED_API_KEY or os.environ.get("EMBED_API_KEY", "")
            if not api_key:
                raise ValueError("请设置 EMBED_API_KEY")
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=cfg.EMBED_API_BASE)

            class APIEmbedder:
                def __init__(self, c, m):
                    self.client = c
                    self.model = m

                def encode(self, texts, **kwargs):
                    resp = self.client.embeddings.create(model=self.model, input=texts)
                    return [d.embedding for d in resp.data]

            return APIEmbedder(client, cfg.EMBED_MODEL_NAME)

        raise ValueError("不支持的 EMBED_BACKEND: {}".format(backend))

    def _load_faiss_and_meta(self):
        import faiss

        idx_path = self.faiss_dir / "index.faiss"
        meta_path = self.faiss_dir / "meta.json"
        if not idx_path.exists():
            raise FileNotFoundError("FAISS 索引不存在: {}".format(idx_path))
        if not meta_path.exists():
            raise FileNotFoundError("FAISS 元数据不存在: {}".format(meta_path))

        index = faiss.read_index(str(idx_path))
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return index, meta["records"]

    def search(self, query, top_k=None):
        query = _normalize_query(query)
        if not query:
            raise ValueError("query 不能为空")

        if self.model is None or self.index is None or self.records is None:
            raise RuntimeError("后端尚未完成初始化")

        if top_k is None:
            top_k = cfg.SEARCH_TOP_K

        import numpy as np

        query_vec = np.array(
            [self.model.encode([query], normalize_embeddings=True)[0]]
        ).astype("float32")
        candidate_k = max(int(top_k) * 8, 20)
        scores, indices = self.index.search(query_vec, candidate_k)
        pairs = list(zip(indices[0], scores[0]))
        query_terms = _extract_query_terms(query)

        results = []
        for idx, score in pairs:
            if idx < 0 or idx >= len(self.records):
                continue
            rec = self.records[idx]
            meta = rec["metadata"]
            source = meta.get("source", "未知")
            text = rec["text"]
            rerank_score = float(score) + _score_keyword_hits(text, source, query_terms)
            results.append(
                {
                    "text": text,
                    "source": source,
                    "chunk_index": meta.get("chunk_index", 0),
                    "score": round(rerank_score, 4),
                    "char_start": meta.get("char_start", 0),
                    "total_chunks": meta.get("total_chunks", 1),
                }
            )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[: int(top_k)]


def _parse_args(argv):
    host = "127.0.0.1"
    port = 8765
    db_dir = None

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--host" and i + 1 < len(argv):
            host = argv[i + 1]
            i += 2
        elif arg == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
            i += 2
        elif arg == "--db" and i + 1 < len(argv):
            db_dir = argv[i + 1]
            i += 2
        else:
            raise ValueError("未知参数: {}".format(arg))

    return host, port, db_dir


def make_handler(backend):
    class SearchHandler(BaseHTTPRequestHandler):
        server_version = "SearchBackend/1.0"

        def _send_json(self, status_code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self):
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0:
                return {}
            raw = self.rfile.read(content_length)
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def do_GET(self):
            parsed = urlparse(self.path)

            if parsed.path == "/health":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "status": "ready",
                        "db_dir": str(backend.db_dir.resolve()),
                        "faiss_dir": str(backend.faiss_dir.resolve()),
                        "records": len(backend.records or []),
                        "embed_backend": cfg.EMBED_BACKEND,
                        "model": cfg.LOCAL_MODEL_NAME
                        if cfg.EMBED_BACKEND.lower() == "local"
                        else cfg.EMBED_MODEL_NAME,
                    },
                )
                return

            if parsed.path == "/search":
                params = parse_qs(parsed.query, keep_blank_values=True)
                query = (params.get("q") or [""])[0]
                top_k_raw = (params.get("top_k") or [str(cfg.SEARCH_TOP_K)])[0]
                try:
                    top_k = int(top_k_raw)
                    results = backend.search(query, top_k=top_k)
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "query": query,
                            "top_k": top_k,
                            "count": len(results),
                            "results": results,
                        },
                    )
                except Exception as e:
                    self._send_json(400, {"ok": False, "error": str(e)})
                return

            self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path != "/search":
                self._send_json(404, {"ok": False, "error": "not found"})
                return

            try:
                payload = self._read_json_body()
                query = str(payload.get("query", ""))
                top_k = int(payload.get("top_k", cfg.SEARCH_TOP_K))
                results = backend.search(query, top_k=top_k)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "query": query,
                        "top_k": top_k,
                        "count": len(results),
                        "results": results,
                    },
                )
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})

        def log_message(self, format_str, *args):
            message = "%s - - [%s] %s" % (
                self.address_string(),
                self.log_date_time_string(),
                format_str % args,
            )
            print(message)

    return SearchHandler


def main():
    _configure_stdio()
    try:
        host, port, db_dir = _parse_args(sys.argv[1:])
    except Exception as e:
        print("用法: python search_backend.py [--host 127.0.0.1] [--port 8765] [--db TXT归档目录]")
        print("参数错误: {}".format(e))
        sys.exit(1)

    backend = SearchBackend(db_dir=db_dir)

    print("正在加载检索后端...")
    print("  数据目录: {}".format(backend.db_dir.resolve()))
    print("  索引目录: {}".format(backend.faiss_dir.resolve()))
    backend.load()
    print("加载完成")
    print("  记录数: {}".format(len(backend.records or [])))
    print("  Embedding 后端: {}".format(cfg.EMBED_BACKEND))

    server = ThreadingHTTPServer((host, port), make_handler(backend))
    print("服务已启动: http://{}:{}".format(host, port))
    print("健康检查: http://{}:{}/health".format(host, port))
    print("查询接口: http://{}:{}/search?q=你的问题".format(host, port))
    server.serve_forever()


if __name__ == "__main__":
    main()
