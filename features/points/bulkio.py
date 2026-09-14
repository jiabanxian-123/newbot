# -*- coding: utf-8 -*-
"""⑧ 批量导入导出

支持 CSV（utf-8 / gbk 自动嗅探）与 xlsx（需 openpyxl）。返回 `(rows_skipped, None)`
或 `(None, 错误文案)` —— 非法行只计数不抛，让管理员看得到「跳过几行」。
"""

from core import hub


def _parse_points_rows(data, filename):
    """把上传文件解析为 [(uid, points, nickname)]。支持 CSV(utf-8/gbk) 与 xlsx(需 openpyxl)。
    返回 (rows, err)；err 非 None 表示失败。表头行自动识别，列按表头定位（缺省 用户ID=0列,积分=2列）。"""
    rows_raw = None
    if filename.lower().endswith((".xlsx", ".xls")):
        try:
            import io as _io
            from openpyxl import load_workbook
        except ImportError:
            return None, "服务器未安装 openpyxl，无法读 Excel：请把表格另存为 CSV(逗号分隔) 再导入"
        try:
            wb = load_workbook(_io.BytesIO(data), read_only=True, data_only=True)
            rows_raw = [[("" if cell is None else str(cell.value)) for cell in row] for row in wb.active.iter_rows()]
        except Exception as exc:
            return None, f"Excel 解析失败：{exc}"
    else:
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try: text = data.decode(enc); break
            except (UnicodeDecodeError, LookupError): continue
        if text is None: return None, "无法识别文件编码（请用 UTF-8 或 GBK 编码的 CSV）"
        rows_raw = [line.split(",") for line in text.splitlines() if line.strip()]
    if not rows_raw: return None, "文件内容为空"
    idx_uid, idx_pts, idx_name = 0, 2, 1
    start = 0
    header = [h.strip().strip('"').lower() for h in rows_raw[0]]
    if any("用户id" in h for h in header):
        start = 1
        for i, h in enumerate(header):
            if "用户id" in h: idx_uid = i
            elif h == "积分": idx_pts = i
            elif "昵称" in h or "用户名" in h: idx_name = i
    rows, skipped = [], 0
    for row in rows_raw[start:]:
        cells = [c.strip().strip('"') for c in row] + ["", "", ""]
        try:
            uid, pts = int(cells[idx_uid]), int(float(cells[idx_pts]))
        except (ValueError, IndexError):
            skipped += 1; continue
        if pts < 0: skipped += 1; continue
        rows.append((uid, pts, cells[idx_name] if idx_name < len(cells) else ""))
    return (rows, skipped), None
