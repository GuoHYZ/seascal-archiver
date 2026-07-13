# -*- coding: utf-8 -*-
"""
办公文档批量归档脚本。

功能：
- 递归遍历多级目录，收集所有支持的办公文档
- 支持格式：.pptx  .docx  .xlsx
- 转换为 TXT 纯文本，表格以 Markdown 格式保留
- 镜像输出目录结构
- 生成 _索引.txt 便于检索
- 增量模式：仅处理有变化的文件，大幅提升维护效率

用法：
    python archive.py <输入目录> [输出目录] [--incremental]

示例：
    python archive.py ./文档资料
    python archive.py ./文档资料 ./TXT_归档
    python archive.py ./文档资料 ./TXT_归档 --incremental
    python archive.py D:\\公司文件 --incremental
"""

import sys
import json
import hashlib
import traceback
from pathlib import Path
from datetime import datetime


# ============================================================
# 格式注册表 — 扩展添加只需在下面加一行
# ============================================================

# 格式: 扩展名 -> (模块名, 模块描述, 是否需要额外安装)
SUPPORTED_FORMATS = {
    ".pptx": ("pptx2txt", "PowerPoint 演示文稿", True),
    ".docx": ("docx2txt", "Word 文档", True),
    ".xlsx": ("xlsx2txt", "Excel 电子表格", True),
}

# 旧格式（通过 win32com 调用 MS Office 转为新格式后处理）
LEGACY_FORMATS = {
    ".ppt": "PowerPoint 旧版",
    ".doc": "Word 旧版",
    ".xls": "Excel 旧版",
}


def _get_converter(extension):
    """
    根据扩展名动态导入对应的转换模块，返回 convert 函数。
    返回 None 表示该格式不支持或依赖缺失。
    """
    if extension not in SUPPORTED_FORMATS:
        return None

    module_name, description, required = SUPPORTED_FORMATS[extension]

    if not required:
        return None

    try:
        mod = __import__(module_name)
        return mod.convert
    except ImportError as e:
        print(f"  [警告] 无法加载模块 {module_name}，跳过 {extension} 文件")
        print(f"         缺少依赖: {e}")
        print(f"         请执行: pip install python-pptx python-docx openpyxl")
        return None
    except AttributeError:
        print(f"  [警告] 模块 {module_name} 缺少 convert() 函数，跳过 {extension} 文件")
        return None


# ============================================================
# 文件扫描
# ============================================================

def scan_files(input_dir):
    """
    递归扫描目录，按扩展名收集文件。

    参数:
        input_dir: Path — 要扫描的根目录

    返回: (files_by_ext, legacy_files, total_count) 其中：
        files_by_ext:  dict {扩展名: [Path, ...]}
        legacy_files:  list of Path — 旧格式文件
        total_count:   int — 总文件数（不含临时文件）
    """
    files_by_ext = {}
    legacy_files = []
    total_count = 0

    all_extensions = set(SUPPORTED_FORMATS.keys()) | set(LEGACY_FORMATS.keys())

    for file_path in sorted(input_dir.rglob("*")):
        if not file_path.is_file():
            continue

        # 跳过临时文件和隐藏文件
        if file_path.name.startswith("~$") or file_path.name.startswith("."):
            continue

        ext = file_path.suffix.lower()
        if ext not in all_extensions:
            continue

        total_count += 1

        if ext in LEGACY_FORMATS:
            legacy_files.append(file_path)
        else:
            files_by_ext.setdefault(ext, []).append(file_path)

    return files_by_ext, legacy_files, total_count


# ============================================================
# 目录镜像与输出路径
# ============================================================

def _mirror_output_path(source_file, input_root, output_root):
    """
    计算输出文件的路径，保持相对于输入根的目录结构。
    """
    rel_path = source_file.relative_to(input_root)
    output_path = output_root / rel_path.parent / (rel_path.stem + ".txt")
    return output_path


# ============================================================
# 元数据管理（增量更新核心）
# ============================================================

META_FILENAME = "_archive_meta.json"


def _load_meta(output_dir):
    """
    加载元数据文件，返回 dict。
    文件不存在或损坏时返回空 dict。
    """
    meta_path = output_dir / META_FILENAME
    if not meta_path.exists():
        return {}

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "files" in data:
            return data
    except (json.JSONDecodeError, ValueError):
        print("[警告] 元数据文件损坏，将执行全量转换")

    return {}


def _save_meta(output_dir, files_meta):
    """
    保存元数据文件。
    files_meta: dict {相对源路径: {"mtime": float, "size": int, "char_count": int, "output_rel": str}}
    """
    meta_path = output_dir / META_FILENAME
    data = {
        "version": 1,
        "last_run": datetime.now().isoformat(),
        "files": files_meta,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _file_fingerprint(file_path):
    """
    生成文件的跨设备安全"指纹"：(sha256前64KB, 文件大小)

    使用 SHA256 哈希而非 mtime 的原因：
    文件修改时间在跨设备复制后会变化，导致增量模式失效。
    SHA256 + 文件大小组合在任何设备上都一致（只要内容不变）。
    只读取前 64KB，对大型 PPTX/DOCX 仍然快速。
    """
    try:
        stat = file_path.stat()
        size = stat.st_size
        mtime = stat.st_mtime
    except OSError:
        return None

    try:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            chunk = f.read(65536)  # 64KB
            sha256.update(chunk)
        digest = sha256.hexdigest()
        return (digest, size, mtime)
    except (IOError, OSError):
        return (None, size, mtime)


def _classify_files(flat_files, input_root, output_dir, prev_meta, incremental):
    """
    将扫描到的文件分类为：需转换、跳过、过时。

    flat_files: list of (ext, Path) — 所有源文件
    input_root: Path
    output_dir:  Path
    prev_meta:   dict — 上次运行的元数据
    incremental: bool — 是否启用增量模式

    返回: (to_convert, skipped, stale_outputs)
        to_convert:    list of (ext, Path) — 需要转换的文件
        skipped:       list of (ext, Path, str) — 跳过的文件及原因
        stale_outputs: list of Path — 源文件已删除但 TXT 残留的路径
    """
    to_convert = []
    skipped = []
    stale_outputs = []

    prev_files = prev_meta.get("files", {})

    # 收集当前源文件的所有相对路径
    current_rel_paths = set()

    for ext, src_path in flat_files:
        try:
            rel_key = str(src_path.relative_to(input_root)).replace("\\", "/")
        except ValueError:
            rel_key = src_path.name
        current_rel_paths.add(rel_key)

        if not incremental:
            to_convert.append((ext, src_path))
            continue

        # 增量模式：对比指纹（SHA256 + 文件大小，跨设备安全）
        fp = _file_fingerprint(src_path)
        prev_info = prev_files.get(rel_key)

        if fp is None:
            to_convert.append((ext, src_path))
        elif prev_info is None:
            to_convert.append((ext, src_path))
        elif (fp[0] != prev_info.get("sha256") or fp[1] != prev_info.get("size")):
            to_convert.append((ext, src_path))
        else:
            dest = _mirror_output_path(src_path, input_root, output_dir)
            if dest.exists():
                skipped.append((ext, src_path, "未变更"))
            else:
                to_convert.append((ext, src_path))

    # 检测过时输出
    if incremental and prev_files:
        for rel_key, info in prev_files.items():
            if rel_key not in current_rel_paths:
                stale_txt_rel = info.get("output_rel", "")
                if stale_txt_rel:
                    stale_path = output_dir / stale_txt_rel
                    if stale_path.exists():
                        stale_outputs.append(stale_path)

    return to_convert, skipped, stale_outputs


# ============================================================
# 索引文件生成
# ============================================================

def _write_index(output_root, records, skipped_count=0, stale_count=0):
    """
    生成 _索引.txt 文件。

    records: list of dict
        {
            "rel_path": str,
            "source": str,
            "format": str,
            "char_count": int,
            "status": str,
        }
    """
    index_path = output_root / "_索引.txt"

    lines = []
    lines.append("=" * 70)
    lines.append("文档归档索引")
    lines.append("生成时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("可用 grep / Everything 等工具搜索此目录下的 TXT 文件")
    lines.append("=" * 70)
    lines.append("")

    success_count = sum(1 for r in records if r["status"] == "成功")
    failed_count = sum(1 for r in records if r["status"] != "成功"
                       and not r["status"].startswith("跳过"))
    total_chars = sum(r.get("char_count", 0) for r in records)

    lines.append("总记录数: {}".format(len(records)))
    lines.append("成功: {}".format(success_count))
    lines.append("失败: {}".format(failed_count))
    if skipped_count:
        lines.append("跳过(未变更): {}".format(skipped_count))
    if stale_count:
        lines.append("过时(源已删除): {}".format(stale_count))
    lines.append("总字符数: {:,}".format(total_chars))
    lines.append("")
    lines.append("-" * 70)
    lines.append("")

    if success_count > 0:
        lines.append("【成功】")
        lines.append("")
        lines.append("  {fmt:14s} {rel:48s} {chars:>8s}  {mtime:12s}".format(
            fmt="格式", rel="文件路径", chars="字符数", mtime="源文件修改时间"
        ))
        lines.append("  " + "-" * 86)
        for r in records:
            if r["status"] == "成功":
                lines.append(
                    "  [{fmt:12s}] {rel:48s} {chars:>8,}  {mtime:12s}".format(
                        fmt=r["format"],
                        rel=r["rel_path"],
                        chars=r["char_count"],
                        mtime=r.get("source_mtime", "未知"),
                    )
                )
        lines.append("")

    if failed_count > 0:
        lines.append("【失败】")
        lines.append("")
        for r in records:
            if r["status"] != "成功" and not r["status"].startswith("跳过"):
                lines.append("  [{fmt:12s}] {rel:50s} {status}".format(
                    fmt=r["format"],
                    rel=r["rel_path"],
                    status=r["status"],
                ))
        lines.append("")

    if skipped_count > 0:
        lines.append("【跳过 — 未变更】({} 个)".format(skipped_count))
        lines.append("")
        for r in records:
            if r["status"].startswith("跳过"):
                lines.append("  [{fmt:12s}] {rel:50s} {status}".format(
                    fmt=r["format"],
                    rel=r["rel_path"],
                    status=r["status"],
                ))
        lines.append("")

    lines.append("=" * 70)
    lines.append("提示：可使用文本搜索工具（如 grep, Everything）在这些 TXT 文件中检索内容。")

    with open(index_path, "w", encoding="utf-8", errors="replace") as f:
        f.write("\n".join(lines) + "\n")

    return index_path


# ============================================================
# 主处理流程
# ============================================================

def archive(input_dir, output_dir, incremental=False, clean_stale=False):
    """
    主归档函数：扫描、转换、生成索引。

    参数:
        input_dir:    str 或 Path — 包含文档的根目录
        output_dir:   str 或 Path — TXT 输出根目录
        incremental:  bool — 是否启用增量模式（默认 False，全量转换）
        clean_stale:  bool — 是否删除过时的输出文件（源文件已删除的 TXT）
    """
    input_dir = Path(input_dir).resolve()
    output_dir = Path(output_dir).resolve()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- 输入检查 ----
    if not input_dir.is_dir():
        print("[错误] 输入目录不存在: {}".format(input_dir))
        return

    # ---- 扫描文件 ----
    print("\n[{}] 正在扫描: {}".format(now_str, input_dir))
    files_by_ext, legacy_files, total_count = scan_files(input_dir)

    if total_count == 0:
        print("[警告] 未找到任何支持的文档文件")
        return

    print("找到 {} 个文档文件".format(total_count))
    for ext, files in sorted(files_by_ext.items()):
        print("  {}: {} 个".format(ext, len(files)))

    # ---- 旧格式转换：.doc/.ppt/.xls → 新格式 ----
    if legacy_files:
        print("\n正在转换 {} 个旧格式文件...".format(len(legacy_files)))
        legacy_success = 0
        legacy_failed = 0

        # 一次性导入 legacy2new（避免循环内重复 import）
        try:
            import legacy2new
            HAS_LEGACY = True
        except ImportError:
            HAS_LEGACY = False
            print("  [警告] pywin32 未安装，无法转换旧格式")
            print("         请安装: pip install pywin32")

        if HAS_LEGACY:
            for lf in legacy_files:
                try:
                    new_path = legacy2new.convert(lf)
                except Exception as e:
                    print("  [错误] 旧格式转换异常: {}".format(e))
                    new_path = None

                if new_path and new_path.exists():
                    ext_new = new_path.suffix.lower()
                    existing_list = files_by_ext.setdefault(ext_new, [])
                    if new_path not in existing_list:
                        existing_list.append(new_path)
                        legacy_success += 1
                        print("  [OK] {} → {}".format(lf.name, new_path.name))
                    else:
                        # .doc 转换后发现 .docx 已在目录中 → 无需重复追加
                        print("  [跳过] {} → {} (已存在)".format(lf.name, new_path.name))
                else:
                    legacy_failed += 1
                    print("  [FAIL] {} 转换失败，跳过".format(lf.name))

        if legacy_success:
            print("  旧格式转换完成: 成功 {}, 失败 {}".format(legacy_success, legacy_failed))

    # ---- 创建输出目录 ----
    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- 增量模式：加载元数据并分类 ----
    prev_meta = _load_meta(output_dir) if incremental else {}
    mode_label = "增量模式" if incremental else "全量模式"

    # 展平文件列表
    flat_files = []
    for ext, files in sorted(files_by_ext.items()):
        for f in files:
            flat_files.append((ext, f))

    to_convert, skipped, stale_outputs = _classify_files(
        flat_files, input_dir, output_dir, prev_meta, incremental
    )

    skipped_count = len(skipped)
    stale_count = len(stale_outputs)

    # 处理过时文件
    if clean_stale and stale_outputs:
        for p in stale_outputs:
            try:
                p.unlink()
                print("[清理] 已删除过时 TXT: {}".format(p.name))
            except OSError:
                pass

    # ---- 处理文件 ----
    if incremental:
        if not to_convert and not stale_outputs:
            print(
                "\n[{}]  所有文件均为最新，无需转换。{} 个文件保持。".format(
                    mode_label, skipped_count
                )
            )
        else:
            print(
                "\n[{}] 需转换: {} 个, 跳过: {} 个, 过时: {} 个".format(
                    mode_label, len(to_convert), skipped_count, stale_count
                )
            )

    print("\n开始转换，输出到: {}\n".format(output_dir))

    records = []
    new_meta = {}
    success = 0
    failed = 0
    total_to_process = len(to_convert)
    processed = 0

    # ---- 处理跳过的文件（先记录到 records 中保持索引顺序） ----
    for ext, src_path, reason in sorted(skipped, key=lambda x: x[1]):
        dest = _mirror_output_path(src_path, input_dir, output_dir)
        rel_output = str(dest.relative_to(output_dir))
        try:
            char_count = len(dest.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            char_count = 0
        # 获取源文件修改时间
        src_mtime = ""
        if incremental:
            try:
                rel_key2 = str(src_path.relative_to(input_dir)).replace("\\", "/")
            except ValueError:
                rel_key2 = src_path.name
            prev_info2 = prev_meta.get("files", {}).get(rel_key2, {})
            if prev_info2 and prev_info2.get("mtime"):
                src_mtime = datetime.fromtimestamp(prev_info2["mtime"]).strftime("%Y-%m-%d")
        if not src_mtime:
            try:
                src_mtime = datetime.fromtimestamp(src_path.stat().st_mtime).strftime("%Y-%m-%d")
            except OSError:
                src_mtime = "未知"

        records.append({
            "rel_path": rel_output,
            "source": src_path.name,
            "format": SUPPORTED_FORMATS[ext][1],
            "char_count": char_count,
            "status": "跳过: {}".format(reason),
            "source_mtime": src_mtime,
        })
        # 保留元数据
        try:
            rel_key = str(src_path.relative_to(input_dir)).replace("\\", "/")
        except ValueError:
            rel_key = src_path.name
        prev_info = prev_meta.get("files", {}).get(rel_key, {})
        if prev_info:
            new_meta[rel_key] = prev_info

    # ---- 处理需要转换的文件 ----
    # 按扩展名分组以便先加载 converter
    to_convert_by_ext = {}
    for ext, src_path in to_convert:
        to_convert_by_ext.setdefault(ext, []).append(src_path)

    for ext in sorted(to_convert_by_ext.keys()):
        converter = _get_converter(ext)
        if converter is None:
            for src_path in to_convert_by_ext[ext]:
                processed += 1
                dest = _mirror_output_path(src_path, input_dir, output_dir)
                try:
                    src_mtime = datetime.fromtimestamp(src_path.stat().st_mtime).strftime("%Y-%m-%d")
                except OSError:
                    src_mtime = "未知"
                records.append({
                    "rel_path": str(dest.relative_to(output_dir)),
                    "source": src_path.name,
                    "format": SUPPORTED_FORMATS[ext][1],
                    "char_count": 0,
                    "status": "失败: 缺少依赖或模块",
                    "source_mtime": src_mtime,
                })
                failed += 1
            continue

        fmt_desc = SUPPORTED_FORMATS[ext][1]

        for src_path in to_convert_by_ext[ext]:
            processed += 1
            dest = _mirror_output_path(src_path, input_dir, output_dir)

            try:
                char_count = converter(str(src_path), str(dest))
                success += 1
                print("[{:4d}/{:4d}] [OK] [{}] {} ({:,} 字符)".format(
                    processed, total_to_process, ext, src_path.name, char_count
                ))
                try:
                    src_mtime = datetime.fromtimestamp(src_path.stat().st_mtime).strftime("%Y-%m-%d")
                except OSError:
                    src_mtime = "未知"
                records.append({
                    "rel_path": str(dest.relative_to(output_dir)),
                    "source": src_path.name,
                    "format": fmt_desc,
                    "char_count": char_count,
                    "status": "成功",
                    "source_mtime": src_mtime,
                })

                # 更新元数据
                try:
                    rel_key = str(src_path.relative_to(input_dir)).replace("\\", "/")
                except ValueError:
                    rel_key = src_path.name
                fp = _file_fingerprint(src_path)
                if fp:
                    new_meta[rel_key] = {
                        "sha256": fp[0],
                        "size": fp[1],
                        "mtime": fp[2],
                        "char_count": char_count,
                        "output_rel": str(dest.relative_to(output_dir)),
                    }

            except Exception as e:
                failed += 1
                print("[{:4d}/{:4d}] [FAIL] [{}] {}".format(
                    processed, total_to_process, ext, src_path.name
                ))
                print("          原因: {}".format(e))
                traceback.print_exc()
                try:
                    fail_mtime = datetime.fromtimestamp(src_path.stat().st_mtime).strftime("%Y-%m-%d")
                except OSError:
                    fail_mtime = "未知"
                records.append({
                    "rel_path": str(dest.relative_to(output_dir)),
                    "source": src_path.name,
                    "format": fmt_desc,
                    "char_count": 0,
                    "status": "失败: {}".format(e),
                    "source_mtime": fail_mtime,
                })

    # ---- 保存元数据 ----
    if incremental:
        _save_meta(output_dir, new_meta)

    # ---- 生成索引 ----
    print("\n正在生成索引...")
    index_path = _write_index(
        output_dir, records,
        skipped_count=skipped_count,
        stale_count=stale_count,
    )

    # ---- 汇总 ----
    print("\n" + "=" * 50)
    print("归档完成 - {}".format(now_str))
    print("  成功: {}  失败: {}  跳过: {}".format(success, failed, skipped_count))
    if stale_count:
        print("  过时(TXT残留): {}".format(stale_count))
    print("  总计源文件: {}".format(total_count))
    print("  输出目录: {}".format(output_dir))
    print("  索引文件: {}".format(index_path))
    print("=" * 50)

    if incremental and not prev_meta:
        print("\n提示：下次运行 --incremental 将仅处理变更文件，速度更快。")


# ============================================================
# 命令行入口
# ============================================================

def main():
    # 解析参数
    args = sys.argv[1:]

    incremental = False
    clean_stale = False

    # 过滤标志参数
    positional = []
    for a in args:
        if a in ("--incremental", "-i"):
            incremental = True
        elif a in ("--clean-stale", "--clean"):
            clean_stale = True
        else:
            positional.append(a)

    if len(positional) < 1:
        print(__doc__)
        print("用法: python archive.py <输入目录> [输出目录] [--incremental] [--clean-stale]")
        print()
        print("参数说明:")
        print("  输入目录         包含文档的根目录（必填）")
        print("  输出目录         TXT 输出目录（可选，默认: 输入目录名_TXT归档）")
        print("  --incremental    增量模式：仅处理有变化的文件")
        print("  --clean-stale    清理过时的 TXT 文件（源文件已删除）")
        print()
        print("示例:")
        print("  python archive.py ./文档资料                           # 全量转换")
        print("  python archive.py ./文档资料 ./TXT_归档                # 指定输出")
        print("  python archive.py ./文档资料 --incremental              # 首次=全量，后续=增量")
        print("  python archive.py ./文档资料 -i --clean               # 增量+清理过时文件")
        print("  python archive.py D:\\公司文件 --incremental")
        sys.exit(1)

    input_dir = positional[0]
    output_dir = positional[1] if len(positional) > 1 else None

    if output_dir is None:
        input_path = Path(input_dir)
        output_dir = input_path.parent / "{}_TXT归档".format(input_path.name)

    archive(input_dir, output_dir, incremental=incremental, clean_stale=clean_stale)


if __name__ == "__main__":
    main()