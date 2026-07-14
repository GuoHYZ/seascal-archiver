# -*- coding: utf-8 -*-
"""
Seascal's Archiver — 统一交互入口

提供文档转换、索引构建、检索等功能的全交互式界面。
也可通过命令行参数直接调用对应功能。

用法:
    python main.py                    # 交互式菜单
    python main.py convert <目录> [输出目录]  # 转换文档
    python main.py build <归档目录>          # 构建索引
    python main.py search <查询> <归档目录>  # 检索
    python main.py backend <归档目录>        # 启动检索后端
"""

import sys
from pathlib import Path

# 便携包兼容
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _get_dir(prompt, must_exist=True):
    """交互式获取目录路径。"""
    while True:
        path = input(prompt).strip().strip('"').strip("'")
        if not path:
            print("  输入不能为空")
            continue
        p = Path(path)
        if must_exist and not p.is_dir():
            print("  目录不存在: {}".format(p))
            continue
        return str(p.resolve())


def _cmd_convert():
    """文档转换。"""
    from archive import archive

    print("\n===== 文档转换 =====")
    src = _get_dir("请输入文档目录路径: ")

    use_incremental = input("增量模式？(Y/n): ").strip().lower()
    incremental = use_incremental not in ("n", "no")

    custom_dst = input("自定义输出目录？(留空使用默认: {}_TXT_archive): ".format(src)).strip()
    if custom_dst:
        dst = custom_dst
    else:
        dst = src + "_TXT_archive"

    print("输出目录: {}".format(dst))
    archive(src, dst, incremental=incremental)


def _cmd_build():
    """索引构建。"""
    from search.build_rag import build_index

    print("\n===== 构建索引 =====")
    txt_dir = _get_dir("请输入 TXT 归档目录路径: ")
    use_force = input("强制全量重建？(y/N): ").strip().lower()
    force = use_force in ("y", "yes")

    build_index(txt_dir=txt_dir, force=force)


def _cmd_search():
    """交互式检索。"""
    import rag_config as cfg

    print("\n===== 交互检索 =====")
    db_dir = _get_dir("请输入 TXT 归档目录路径: ")
    print("检索模式: {}".format(
        "混合检索 (BM25 + 向量)" if cfg.HYBRID_ENABLED else "纯向量"
    ))
    print("输入空行退出\n")

    # 复用 SearchBackend（避免重复加载模型）
    from search.search_backend import SearchBackend
    backend = SearchBackend(db_dir=db_dir)
    backend.load()

    while True:
        query = input("查询> ").strip()
        if not query:
            print("已退出检索")
            break

        try:
            chunks = backend.search(query, top_k=cfg.SEARCH_TOP_K)
        except Exception as e:
            print("  检索失败: {}\n".format(e))
            continue

        if not chunks:
            print("  未找到相关内容\n")
            continue

        for i, c in enumerate(chunks, 1):
            source = c.get("source", "未知")
            score = c.get("score", 0)
            text = c.get("text", "")
            if len(text) > 300:
                text = text[:300] + "..."
            bm25 = c.get("bm25_score")
            extra = " BM25:{:.3f}".format(bm25) if bm25 is not None else ""
            print("  #{:<2} [{:.4f}{}] {}".format(i, score, extra, source))
            print("      {}\n".format(text.replace(chr(10), " ")))


def _cmd_backend():
    """启动检索后端。"""
    from search.search_backend import SearchBackend, make_handler
    from http.server import ThreadingHTTPServer

    print("\n===== 启动检索后端 =====")
    db_dir = _get_dir("请输入 TXT 归档目录路径: ")

    backend = SearchBackend(db_dir=db_dir)
    backend.load()

    host = "127.0.0.1"
    port = 8765

    server = ThreadingHTTPServer((host, port), make_handler(backend))
    server.allow_reuse_address = True

    print("\n服务已启动: http://{}:{}".format(host, port))
    print("健康检查: http://{}:{}/health".format(host, port))
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭服务...")
    finally:
        server.server_close()
        print("服务已关闭")


def _interactive_menu():
    """交互式菜单。"""
    while True:
        print("\n" + "=" * 40)
        print("  Seascal's Archiver")
        print("=" * 40)
        print("  1. 转换文档     (Office → TXT)")
        print("  2. 构建索引     (FAISS + BM25)")
        print("  3. 交互检索     (混合检索)")
        print("  4. 启动后端     (HTTP API)")
        print("  0. 退出")
        print("-" * 40)

        choice = input("请选择 [0-4]: ").strip()
        try:
            if choice == "0":
                print("再见！")
                break
            elif choice == "1":
                _cmd_convert()
            elif choice == "2":
                _cmd_build()
            elif choice == "3":
                _cmd_search()
            elif choice == "4":
                _cmd_backend()
            else:
                print("无效选项，请输入 0-4")
        except KeyboardInterrupt:
            print("\n操作已取消")
        except Exception as e:
            print("\n[错误] {}".format(e))
            import traceback
            traceback.print_exc()
            print()


def main():
    args = sys.argv[1:]

    if not args:
        _interactive_menu()
        return

    cmd = args[0].lower()

    if cmd == "convert":
        if len(args) < 2:
            _cmd_convert()
        else:
            src = args[1]
            dst = args[2] if len(args) > 2 else src + "_TXT_archive"
            from archive import archive
            archive(src, dst, incremental=True)

    elif cmd == "build":
        if len(args) < 2:
            _cmd_build()
        else:
            from search.build_rag import build_index
            build_index(txt_dir=args[1])

    elif cmd == "search":
        if len(args) < 3:
            if len(args) == 2:
                print("用法: python main.py search <查询> <归档目录>")
                sys.exit(1)
            _cmd_search()
        else:
            from search.search_backend import SearchBackend
            backend = SearchBackend(db_dir=args[2])
            backend.load()
            results = backend.search(args[1])
            for i, c in enumerate(results, 1):
                print("#{}  {}  ({:.4f})".format(i, c.get("source", "未知"), c.get("score", 0)))
                text = c.get("text", "")
                if len(text) > 200:
                    print(text[:200] + "...\n")
                else:
                    print(text + "\n")

    elif cmd == "backend":
        if len(args) < 2:
            _cmd_backend()
        else:
            from search.search_backend import SearchBackend, make_handler
            from http.server import ThreadingHTTPServer
            import signal

            backend = SearchBackend(db_dir=args[1])
            backend.load()

            host, port = "127.0.0.1", 8765
            server = ThreadingHTTPServer((host, port), make_handler(backend))
            server.allow_reuse_address = True

            def _shutdown(s, f):
                server.shutdown()

            signal.signal(signal.SIGINT, _shutdown)
            print("服务已启动: http://{}:{}".format(host, port))
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()

    else:
        print("未知命令: {}".format(cmd))
        print("用法: python main.py [convert|build|search|backend]")
        sys.exit(1)


if __name__ == "__main__":
    main()