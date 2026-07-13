# -*- coding: utf-8 -*-
"""
本地 RAG 查询后端。

用途：
1. 启动时只加载一次 embedding 模型和 FAISS/BM25 索引
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
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rag_config as cfg
from rag_utils import _configure_stdio, load_embedder, BM25Index, rrf_fusion


def _normalize_query(query):
    return re.sub(r"\s+", " ", query).strip()


class SearchBackend:
    def __init__(self, db_dir=None):
        self.db_dir = Path(db_dir) if db_dir else Path(cfg.TXT_ARCHIVE_DIR)
        self.faiss_dir = self.db_dir.resolve() / cfg.FAISS_SUBDIR
        self.model = None
        self.index = None
        self.records = None
        self.bm25 = None

    def load(self):
        if not self.faiss_dir.exists():
            raise FileNotFoundError(
                "向量索引目录不存在: {}。请先运行 python build_rag.py".format(self.faiss_dir)
            )

        self.index, self.records = self._load_faiss_and_meta()
        self.model = load_embedder()
        self.bm25 = self._load_bm25()

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

    def _load_bm25(self):
        bm25_path = self.faiss_dir / "bm25.pkl"
        if not bm25_path.exists():
            print("  [提示] BM25 索引不存在，使用纯向量检索模式。")
            print("         重新运行 build_rag.py 可构建 BM25 索引。")
            return None
        try:
            bm25 = BM25Index.load(bm25_path)
            print("  BM25 索引已加载 ({} 条)".format(bm25.N))
            return bm25
        except Exception as e:
            print("  [警告] BM25 索引加载失败: {}，回退到纯向量模式。".format(e))
            return None

    def _vector_search(self, query_vec, top_k):
        """纯向量检索，使用归一化后内积 = 余弦相似度。"""
        candidate_k = max(int(top_k) * 8, 20)
        scores, indices = self.index.search(query_vec, candidate_k)
        pairs = []
        for idx, score in zip(indices[0], scores[0]):
            if 0 <= idx < len(self.records):
                pairs.append((int(idx), float(score)))
        return pairs[:top_k]

    def _format_result(self, idx, score, source_extra=""):
        """将记录索引 + 分数格式化为结果 dict。"""
        rec = self.records[idx]
        meta = rec["metadata"]
        result = {
            "text": rec["text"],
            "source": meta.get("source", "未知"),
            "chunk_index": meta.get("chunk_index", 0),
            "score": round(score, 4),
            "char_start": meta.get("char_start", 0),
            "total_chunks": meta.get("total_chunks", 1),
        }
        if source_extra:
            result["source_extra"] = source_extra
        return result

    def search(self, query, top_k=None):
        query = _normalize_query(query)
        if not query:
            raise ValueError("query 不能为空")

        if self.model is None or self.index is None or self.records is None:
            raise RuntimeError("后端尚未完成初始化")

        if top_k is None:
            top_k = cfg.SEARCH_TOP_K

        import numpy as np

        # 向量检索
        query_vec = np.array(
            [self.model.encode([query], normalize_embeddings=True)[0]]
        ).astype("float32")

        # 如果启用混合检索且 BM25 可用
        if cfg.HYBRID_ENABLED and self.bm25 is not None:
            recall_k = max(int(top_k) * cfg.HYBRID_RECALL_K, top_k + 5)

            # 两路召回
            bm25_pairs = self.bm25.search(query, top_k=recall_k)
            vector_pairs = self._vector_search(query_vec, recall_k)

            # RRF 融合
            fused = rrf_fusion([bm25_pairs, vector_pairs], k=cfg.RRF_K)
            top_indices = fused[:int(top_k)]

            results = []
            for idx, fused_score in top_indices:
                rec = self.records[idx]
                meta = rec["metadata"]
                # 分别记录各路分数供调试
                bm25_score = next((s for i, s in bm25_pairs if i == idx), 0.0)
                vector_score = next((s for i, s in vector_pairs if i == idx), 0.0)
                results.append({
                    "text": rec["text"],
                    "source": meta.get("source", "未知"),
                    "file_path": str(self.db_dir.resolve() / meta.get("source", "")),
                    "chunk_index": meta.get("chunk_index", 0),
                    "score": round(fused_score, 4),
                    "bm25_score": round(bm25_score, 4),
                    "vector_score": round(vector_score, 4),
                    "char_start": meta.get("char_start", 0),
                    "total_chunks": meta.get("total_chunks", 1),
                })
            return results

        # 纯向量模式：多召回 + 简单关键词微调
        candidate_k = max(int(top_k) * 8, 20)
        pairs = self._vector_search(query_vec, candidate_k)

        import re as _re
        query_terms = list(dict.fromkeys(
            t.strip() for t in _re.split(r"\s+", query) if t.strip()
        ))
        def _keyword_bonus(text, source, terms):
            bonus = 0.0
            sl = source.lower()
            tl = text.lower()
            for t in terms:
                lt = t.lower()
                if lt in sl:
                    bonus += 0.12
                if lt in tl:
                    bonus += 0.03
            return bonus

        results = []
        for idx, score in pairs:
            rec = self.records[idx]
            meta = rec["metadata"]
            total = float(score) + _keyword_bonus(rec["text"], meta.get("source", ""), query_terms)
            r = self._format_result(idx, total, source_extra="vector")
            r["file_path"] = str(self.db_dir.resolve() / meta.get("source", ""))
            results.append(r)
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:int(top_k)]


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
                        "model": (
                            cfg.LOCAL_MODEL_NAME
                            if cfg.EMBED_BACKEND.lower() == "local"
                            else cfg.EMBED_MODEL_NAME
                        ),
                        "hybrid_enabled": cfg.HYBRID_ENABLED and backend.bm25 is not None,
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
    print("  混合检索: {}".format(
        "启用 (BM25 + 向量)" if (cfg.HYBRID_ENABLED and backend.bm25 is not None)
        else "纯向量"
    ))

    server = ThreadingHTTPServer((host, port), make_handler(backend))
    server.allow_reuse_address = True

    def _shutdown(signum, frame):
        print("\n正在关闭服务...")
        server.shutdown()
        print("服务已停止。")

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("服务已启动: http://{}:{}".format(host, port))
    print("健康检查: http://{}:{}/health".format(host, port))
    print("查询接口: http://{}:{}/search?q=你的问题".format(host, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("服务已关闭。")


if __name__ == "__main__":
    main()