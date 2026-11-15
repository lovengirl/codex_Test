"""대시보드 DB 집계 결과를 CSV·Excel·한글 PDF로 직렬화한다."""

import csv
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from io import BytesIO, StringIO
import os
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle


HEADERS = ("구분", "항목", "값", "단위")
CRITERIA = "전체 기간 기준 · 숨김 고객 제외 · 주문 수와 매출은 취소 주문 제외"
METRIC_FIELDS = (
    ("고객 수", "customer_count", "명"),
    ("주문 수", "order_count", "건"),
    ("총매출", "total_sales", "원"),
    ("평균 주문금액", "average_order", "원"),
)
CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def report_rows(data):
    """DB에서 완료한 집계값만 변환한다. 합산·평균 계산은 수행하지 않는다."""
    rows = [("주요 지표", label, data["metrics"][key], unit) for label, key, unit in METRIC_FIELDS]
    rows.extend(("월별 매출", item["month"], item["sales"], "원") for item in data["monthly_sales"])
    return rows


def csv_report(rows):
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    for row in rows:
        # 날짜에 잘못 입력된 텍스트도 Excel에서 수식으로 실행되지 않도록 보존한다.
        writer.writerow([
            "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
            for value in row
        ])
    return output.getvalue().encode("utf-8-sig")


def excel_report(rows, created_at):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "대시보드"
    sheet.sheet_view.showGridLines = False
    for row, value in ((1, "매출 대시보드 보고서"), (2, CRITERIA), (3, created_at)):
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        sheet.cell(row, 1, value)
    sheet["A1"].font = Font(name="맑은 고딕", size=18, bold=True, color="354785")
    sheet["A2"].font = sheet["A3"].font = Font(name="맑은 고딕", size=10, color="667085")
    sheet["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[1].height = 32
    sheet.row_dimensions[2].height = 32
    for row_number, row in enumerate([HEADERS, *rows], start=5):
        sheet.row_dimensions[row_number].height = 24
        for column_number, value in enumerate(row, start=1):
            cell = sheet.cell(row_number, column_number, value)
            if isinstance(value, str):
                cell.data_type = "s"
            cell.font = Font(name="맑은 고딕", size=11, color="252B38")
            cell.alignment = Alignment(vertical="center", horizontal="right" if column_number == 3 else "left")
            if isinstance(value, (int, float)):
                cell.number_format = "#,##0"
            if row_number == 5:
                cell.fill = PatternFill("solid", fgColor="354785")
                cell.font = Font(name="맑은 고딕", size=11, color="FFFFFF", bold=True)
            elif row_number % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F3F4F6")
    for name, width in (("A", 16), ("B", 26), ("C", 26), ("D", 10)):
        sheet.column_dimensions[name].width = width
    sheet.freeze_panes = "C6"
    sheet.auto_filter.ref = f"A5:D{sheet.max_row}"
    sheet.print_title_rows = "1:5"
    sheet.print_options.horizontalCentered = True
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


@lru_cache(maxsize=1)
def korean_pdf_font():
    candidates = (
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf",
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    )
    font_path = next((path for path in candidates if path.is_file()), None)
    if font_path is None:
        raise FileNotFoundError("한글 PDF 생성에 필요한 맑은 고딕 또는 나눔고딕 글꼴을 찾을 수 없습니다.")
    name = "DashboardKorean"
    pdfmetrics.registerFont(TTFont(name, str(font_path)))
    return name


def pdf_report(rows, created_at):
    font = korean_pdf_font()
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=36, rightMargin=36,
                                 topMargin=36, bottomMargin=36, title="매출 대시보드 보고서",
                                 author="Customer Manager")
    body_style = ParagraphStyle("DashboardBody", fontName=font, fontSize=9, leading=15, spaceAfter=8)
    title_style = ParagraphStyle("DashboardTitle", fontName=font, fontSize=19, leading=27,
                                textColor=colors.HexColor("#354785"), spaceAfter=14)
    formatted = [list(HEADERS)] + [
        [category, label, f"{value:,.0f}", unit] for category, label, value, unit in rows
    ]
    table = LongTable(formatted, colWidths=[75, 160, document.width - 288, 53], repeatRows=1,
                      hAlign="LEFT")
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("LEADING", (0, 0), (-1, -1), 14),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#354785")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F4F6")]),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#354785")),
    ]))

    def page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 9)
        canvas.drawRightString(A4[0] - 36, 20, f"{doc.page}쪽")
        canvas.restoreState()

    document.build([
        Paragraph("매출 대시보드 보고서", title_style),
        Paragraph(CRITERIA, body_style),
        Paragraph(created_at + " · 평균 주문금액은 원 단위 반올림", body_style),
        Spacer(1, 8), table,
    ], onFirstPage=page_number, onLaterPages=page_number)
    return output.getvalue()


def dashboard_report(data, file_format):
    rows = report_rows(data)
    created_at = "조회 시각 " + datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S (한국)")
    if file_format == "csv":
        return csv_report(rows)
    if file_format == "xlsx":
        return excel_report(rows, created_at)
    return pdf_report(rows, created_at)
