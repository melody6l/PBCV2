"""PBC需求清单模板处理 - 读取模板、生成清单、解析用户填写结果"""

import os
import uuid
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from excel_handler import normalize_item_name


TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "资料表.xlsx")


def read_template():
    """读取资料表模板，返回科目列表和资料项列表"""
    wb = openpyxl.load_workbook(TEMPLATE_PATH)
    ws = wb["资料表"]

    subjects = set()
    items = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 1):
        # 资料表.xlsx 实际结构: A列=序号, B列=科目, C列=所需PBC
        if not row or len(row) < 2 or not row[1]:
            continue
        seq = int(row[0]) if row[0] is not None else row_idx
        subject = str(row[1]).strip() if row[1] else ""
        pbc_name = str(row[2]).strip() if len(row) > 2 and row[2] else ""
        if not subject:
            continue
        subjects.add(subject)
        items.append({
            "row_index": row_idx,
            "seq": seq,
            "subject": subject,
            "pbc_name": pbc_name,
        })

    wb.close()
    return {
        "subjects": sorted(subjects),
        "items": items,
    }


def generate_checklist(selected_subjects, company_full_name="", company_short_name=""):
    """
    根据用户选择的科目生成PBC需求清单Excel文件，返回文件路径。

    selected_subjects: ["All", "货币资金", "固定资产", ...]
    """
    wb_template = openpyxl.load_workbook(TEMPLATE_PATH)
    ws_template = wb_template["资料表"]

    # 读取模板数据（资料表.xlsx 实际结构: A列=序号, B列=科目, C列=所需PBC）
    all_items = []
    for row in ws_template.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 2 or not row[1]:
            continue
        subject = str(row[1]).strip() if row[1] else ""
        pbc_name = str(row[2]).strip() if len(row) > 2 and row[2] else ""
        if not subject:
            continue
        if subject in selected_subjects:
            all_items.append({
                "subject": subject,
                "pbc_name": pbc_name,
            })

    wb_template.close()

    # 创建输出工作簿
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "核对工作台"

    # 样式定义
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )

    # 表头: 序号 | 科目 | 所需PBC | 需求资料
    headers = ["序号", "科目", "所需PBC", "需求资料"]
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_align
        cell.border = thin_border

    # 填入数据行
    for row_idx, item in enumerate(all_items, 2):
        ws.cell(row=row_idx, column=1, value=row_idx - 1).alignment = center_align
        ws.cell(row=row_idx, column=1).border = thin_border
        ws.cell(row=row_idx, column=2, value=item["subject"]).alignment = center_align
        ws.cell(row=row_idx, column=2).border = thin_border
        ws.cell(row=row_idx, column=3, value=item["pbc_name"]).alignment = center_align
        ws.cell(row=row_idx, column=3).border = thin_border
        # D列 默认=C列内容
        d_cell = ws.cell(row=row_idx, column=4, value=item["pbc_name"])
        d_cell.alignment = center_align
        d_cell.border = thin_border

    # 列宽
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 36
    ws.column_dimensions["D"].width = 42

    # 公司名称 sheet
    ws_company = wb.create_sheet("公司名称")
    ws_company.cell(row=1, column=1, value="公司全称").fill = header_fill
    ws_company.cell(row=1, column=1).font = header_font
    ws_company.cell(row=1, column=1).alignment = center_align
    ws_company.cell(row=1, column=2, value="简称").fill = header_fill
    ws_company.cell(row=1, column=2).font = header_font
    ws_company.cell(row=1, column=2).alignment = center_align
    if company_full_name:
        ws_company.cell(row=2, column=1, value=company_full_name).alignment = center_align
    if company_short_name:
        ws_company.cell(row=2, column=2, value=company_short_name).alignment = center_align
    ws_company.column_dimensions["A"].width = 30
    ws_company.column_dimensions["B"].width = 14

    os.makedirs("uploads", exist_ok=True)
    output_path = os.path.join("uploads", "PBC需求清单_待填写.xlsx")
    wb.save(output_path)
    wb.close()
    return output_path


def read_user_checklist(file_path):
    """
    读取用户填写后的PBC需求清单，解析为前端预览数据。

    返回:
    {
        "companies": [{"full_name": "...", "short_name": "..."}],
        "items": [
            {
                "seq": 1,
                "subject": "All",
                "pbc_name": "科目余额表",
                "demand_name": "PRC Report",
                "company_status": {"CN02": "Y", "CN03": "N", ...},
            }
        ],
        "headers": ["序号", "科目", "所需PBC", "需求资料", "CN02", "CN03", ...]
    }
    """
    wb = openpyxl.load_workbook(file_path)

    # 读取公司名称
    companies = []
    if "公司名称" in wb.sheetnames:
        ws_company = wb["公司名称"]
        for row in ws_company.iter_rows(min_row=2, values_only=True):
            if row and row[0]:
                companies.append({
                    "full_name": str(row[0]).strip(),
                    "short_name": str(row[1]).strip() if row[1] else "",
                })

    # 读取核对工作台
    ws = None
    for sname in wb.sheetnames:
        if sname in ("资料预览", "核对工作台", "示例"):
            ws = wb[sname]
            break
    if ws is None:
        ws = wb.active

    headers = []
    items = []
    for row_idx, row in enumerate(ws.iter_rows(values_only=False)):
        raw_vals = [c.value for c in row]
        if row_idx == 0:
            headers = [str(v) if v else "" for v in raw_vals]
            continue

        # A=序号, B=科目, C=所需PBC, D=需求资料, E~=各公司状态
        if not raw_vals[1] and not raw_vals[2]:
            continue

        company_status = {}
        for ci in range(4, len(raw_vals)):
            val = raw_vals[ci]
            col_header = headers[ci] if ci < len(headers) else ""
            if col_header and val is not None:
                sv = str(val).strip().upper()
                if sv in ("Y", "YES", "1", "TRUE"):
                    company_status[col_header] = "Y"
                elif sv in ("N/A", "NA"):
                    company_status[col_header] = "N/A"
                elif sv in ("不完整", "INCOMPLETE", "I"):
                    company_status[col_header] = "不完整"
                elif sv in ("N", "NO", "0", "FALSE"):
                    company_status[col_header] = "N"
                elif sv and str(val).strip() == "不完整":
                    company_status[col_header] = "不完整"
                elif sv:
                    company_status[col_header] = "N"

        items.append({
            "row_index": row_idx,
            "seq": int(raw_vals[0]) if raw_vals[0] is not None else row_idx,
            "subject": str(raw_vals[1]).strip() if len(raw_vals) > 1 and raw_vals[1] else "",
            "pbc_name": str(raw_vals[2]).strip() if len(raw_vals) > 2 and raw_vals[2] else "",
            "demand_name": str(raw_vals[3]).strip() if len(raw_vals) > 3 and raw_vals[3] else "",
            "company_status": company_status,
        })

    wb.close()

    company_names = [c["short_name"] or c["full_name"] for c in companies]
    return {
        "companies": companies,
        "company_names": company_names,
        "items": items,
        "headers": headers[:4] + company_names,
    }


def update_cell_status(file_path, row_index, company_name, status):
    """更新某个单元格的获取状态（Y/N/N/A）"""
    wb = openpyxl.load_workbook(file_path)
    ws = None
    for sname in wb.sheetnames:
        if sname in ("资料预览", "核对工作台", "示例"):
            ws = wb[sname]
            break
    if ws is None:
        ws = wb.active

    # 找到公司名称所在列
    target_col = None
    for col_idx in range(5, ws.max_column + 1):
        val = ws.cell(row=1, column=col_idx).value
        if val and str(val).strip() == company_name:
            target_col = col_idx
            break

    if target_col is None:
        wb.close()
        raise ValueError(f"未找到公司列: {company_name}")

    cell = ws.cell(row=row_index, column=target_col)

    # 颜色样式
    green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    gray_fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
    orange_fill = PatternFill(start_color="FFD966", end_color="FFD966", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center")

    cell.value = status
    cell.alignment = center_align
    if status == "Y":
        cell.fill = green_fill
    elif status == "不完整":
        cell.fill = orange_fill
    elif status == "N/A":
        cell.fill = gray_fill
    else:
        cell.fill = red_fill

    wb.save(file_path)
    wb.close()
    return True


def export_checklist_with_status(file_path):
    """导出当前填写好状态的Excel文件"""
    return file_path
