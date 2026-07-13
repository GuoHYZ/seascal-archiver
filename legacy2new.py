# -*- coding: utf-8 -*-
"""
旧格式 → 新格式转换模块。

依赖：Microsoft Office (Word/Excel/PowerPoint) 已安装，通过 win32com 调用。

功能：
- .doc → .docx
- .ppt → .pptx
- .xls → .xlsx

统一接口：convert(source_path) -> Path | None
    成功返回新文件路径，无法转换返回 None。
"""

from pathlib import Path
from typing import Optional, Union

try:
    import win32com.client

    HAS_COM = True
except ImportError:
    HAS_COM = False


# Office 文件格式常量
# 来源：https://docs.microsoft.com/en-us/office/vba/api/overview/
WD_FORMAT_DOCX = 16  # Word 2007+ XML (.docx)
PP_SAVE_AS_PPTX = 27  # PowerPoint 2007+ (.pptx) — ppSaveAsXMLPresentation
XL_OPEN_XML_WORKBOOK = 51  # Excel 2007+ (.xlsx)


def _get_output_path(src_path):
    # type: (Path) -> Path
    """根据源文件生成新格式的输出路径。"""
    new_ext = {
        ".doc": ".docx",
        ".ppt": ".pptx",
        ".xls": ".xlsx",
    }[src_path.suffix.lower()]
    return src_path.with_suffix(new_ext)


def _safe_quit(app, name=""):
    """安全关闭 Office 应用，避免残留进程。"""
    if app is not None:
        try:
            app.Quit()
        except Exception:
            pass


def doc_to_docx(src_path):
    # type: (Path) -> Optional[Path]
    """.doc → .docx，失败返回 None。"""
    if not HAS_COM:
        print("  [警告] pywin32 未安装，无法转换 .doc")
        return None

    dest = _get_output_path(src_path)
    if dest.exists():
        return dest  # 已转换过

    word = None
    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(str(src_path))
        doc.SaveAs2(str(dest), FileFormat=WD_FORMAT_DOCX)
        doc.Close()
        return dest
    except Exception as e:
        print(f"  [错误] .doc 转换失败: {e}")
        return None
    finally:
        _safe_quit(word)


def ppt_to_pptx(src_path):
    # type: (Path) -> Optional[Path]
    """.ppt → .pptx，失败返回 None。"""
    if not HAS_COM:
        print("  [警告] pywin32 未安装，无法转换 .ppt")
        return None

    dest = _get_output_path(src_path)
    if dest.exists():
        return dest

    ppt = None
    try:
        ppt = win32com.client.Dispatch("PowerPoint.Application")
        presentation = ppt.Presentations.Open(str(src_path), WithWindow=False)
        presentation.SaveAs(str(dest), PP_SAVE_AS_PPTX)
        presentation.Close()
        return dest
    except Exception as e:
        print(f"  [错误] .ppt 转换失败: {e}")
        return None
    finally:
        _safe_quit(ppt)


def xls_to_xlsx(src_path):
    # type: (Path) -> Optional[Path]
    """.xls → .xlsx，失败返回 None。"""
    if not HAS_COM:
        print("  [警告] pywin32 未安装，无法转换 .xls")
        return None

    dest = _get_output_path(src_path)
    if dest.exists():
        return dest

    excel = None
    try:
        excel = win32com.client.Dispatch("Excel.Application")
        excel.Visible = False
        wb = excel.Workbooks.Open(str(src_path))
        wb.SaveAs(str(dest), FileFormat=XL_OPEN_XML_WORKBOOK)
        wb.Close()
        return dest
    except Exception as e:
        print(f"  [错误] .xls 转换失败: {e}")
        return None
    finally:
        _safe_quit(excel)


# 注册表：扩展名 → 转换函数
CONVERTERS = {
    ".doc": doc_to_docx,
    ".ppt": ppt_to_pptx,
    ".xls": xls_to_xlsx,
}


def convert(source_path):
    # type: (Union[str, Path]) -> Optional[Path]
    """
    统一转换接口：旧格式 → 新格式。

    参数:
        source_path: str 或 Path — .doc / .ppt / .xls 文件路径

    返回:
        成功 → 新文件的 Path
        失败 → None
    """
    source_path = Path(source_path)
    ext = source_path.suffix.lower()

    converter = CONVERTERS.get(ext)
    if converter is None:
        print(f"  [警告] 不支持的旧格式: {ext}")
        return None

    return converter(source_path)


# ============================================================
# 独立运行：测试转换
# ============================================================
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python legacy2new.py <旧文件.doc|ppt|xls>")
        sys.exit(1)

    result = convert(sys.argv[1])
    if result:
        print(f"转换成功: {result}")
    else:
        print("转换失败")