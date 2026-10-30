from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table as ExcelTable, TableStyleInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table as PdfTable,
    TableStyle,
)


@dataclass(frozen=True)
class ExportColumn:
    key: str
    label: str
    width: int


MIME_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "xlsx": (
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"
    ),
    "pdf": "application/pdf",
}


FILE_EXTENSIONS = {
    "txt": "txt",
    "xlsx": "xlsx",
    "pdf": "pdf",
}


def build_export(
    *,
    file_format: str,
    report_code: str,
    report_title: str,
    columns: list[ExportColumn],
    rows: list[dict[str, Any]],
) -> tuple[bytes, str, str]:
    normalized = file_format.lower()

    generated_at = datetime.now().astimezone()

    if normalized == "txt":
        payload = build_txt(
            report_title=report_title,
            columns=columns,
            rows=rows,
            generated_at=generated_at,
        )

    elif normalized == "xlsx":
        payload = build_xlsx(
            report_title=report_title,
            columns=columns,
            rows=rows,
            generated_at=generated_at,
        )

    elif normalized == "pdf":
        payload = build_pdf(
            report_title=report_title,
            columns=columns,
            rows=rows,
            generated_at=generated_at,
        )

    else:
        raise ValueError(
            f"Unsupported export format: {file_format}"
        )

    timestamp = generated_at.strftime(
        "%Y%m%d_%H%M%S"
    )

    extension = FILE_EXTENSIONS[normalized]

    filename = (
        f"retail_{report_code}_"
        f"{timestamp}.{extension}"
    )

    return (
        payload,
        MIME_TYPES[normalized],
        filename,
    )


def display_value(
    value: Any,
) -> str:
    if value is None:
        return ""

    return str(value)


# ============================================================
# TXT
# ============================================================


def build_txt(
    *,
    report_title: str,
    columns: list[ExportColumn],
    rows: list[dict[str, Any]],
    generated_at: datetime,
) -> bytes:
    table_rows = [
        [
            display_value(
                row.get(column.key)
            )
            for column in columns
        ]
        for row in rows
    ]

    widths = []

    for index, column in enumerate(columns):
        values = [
            len(column.label),
            *[
                len(row[index])
                for row in table_rows
            ],
        ]

        widths.append(
            max(values)
        )

    def render_row(
        values: Iterable[str],
    ) -> str:
        return " | ".join(
            value.ljust(widths[index])
            for index, value in enumerate(values)
        )

    header = [
        column.label
        for column in columns
    ]

    separator = "-+-".join(
        "-" * width
        for width in widths
    )

    lines = [
        report_title,
        (
            "Generated: "
            + generated_at.strftime(
                "%Y-%m-%d %H:%M:%S %z"
            )
        ),
        f"Rows: {len(rows)}",
        "",
        render_row(header),
        separator,
    ]

    for row in table_rows:
        lines.append(
            render_row(row)
        )

    text = "\r\n".join(lines) + "\r\n"

    # UTF-8 BOM improves compatibility with
    # Windows Notepad and older corporate tools.
    return (
        b"\xef\xbb\xbf"
        + text.encode("utf-8")
    )


# ============================================================
# XLSX
# ============================================================


def build_xlsx(
    *,
    report_title: str,
    columns: list[ExportColumn],
    rows: list[dict[str, Any]],
    generated_at: datetime,
) -> bytes:
    workbook = Workbook()

    worksheet = workbook.active
    worksheet.title = "Report"

    column_count = len(columns)

    worksheet.merge_cells(
        start_row=1,
        start_column=1,
        end_row=1,
        end_column=column_count,
    )

    title_cell = worksheet.cell(
        row=1,
        column=1,
        value=report_title,
    )

    title_cell.font = Font(
        bold=True,
        size=16,
    )

    title_cell.alignment = Alignment(
        vertical="center",
    )

    worksheet.merge_cells(
        start_row=2,
        start_column=1,
        end_row=2,
        end_column=column_count,
    )

    meta_cell = worksheet.cell(
        row=2,
        column=1,
        value=(
            "Generated: "
            + generated_at.strftime(
                "%Y-%m-%d %H:%M:%S %z"
            )
            + f" | Rows: {len(rows)}"
        ),
    )

    meta_cell.font = Font(
        size=10,
    )

    header_row = 4

    header_fill = PatternFill(
        fill_type="solid",
        fgColor="0B2D49",
    )

    header_font = Font(
        bold=True,
        color="FFFFFF",
    )

    for index, column in enumerate(
        columns,
        start=1,
    ):
        cell = worksheet.cell(
            row=header_row,
            column=index,
            value=column.label,
        )

        cell.fill = header_fill
        cell.font = header_font

        cell.alignment = Alignment(
            vertical="center",
        )

    for row_index, row in enumerate(
        rows,
        start=header_row + 1,
    ):
        for column_index, column in enumerate(
            columns,
            start=1,
        ):
            worksheet.cell(
                row=row_index,
                column=column_index,
                value=row.get(column.key),
            )

    last_row = (
        header_row + max(len(rows), 1)
    )

    if rows:
        table_range = (
            f"A{header_row}:"
            f"{excel_column_letter(column_count)}"
            f"{last_row}"
        )

        excel_table = ExcelTable(
            displayName="RetailReport",
            ref=table_range,
        )

        style = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )

        excel_table.tableStyleInfo = style

        worksheet.add_table(
            excel_table
        )

    worksheet.freeze_panes = (
        f"A{header_row + 1}"
    )


    for index, column in enumerate(
        columns,
        start=1,
    ):
        letter = excel_column_letter(index)

        worksheet.column_dimensions[
            letter
        ].width = column.width

    worksheet.row_dimensions[1].height = 24

    output = BytesIO()

    workbook.save(output)

    return output.getvalue()


def excel_column_letter(
    column_number: int,
) -> str:
    result = ""

    number = column_number

    while number:
        number, remainder = divmod(
            number - 1,
            26,
        )

        result = (
            chr(65 + remainder)
            + result
        )

    return result


# ============================================================
# PDF
# ============================================================


def find_pdf_fonts() -> tuple[str, str]:
    candidates = [
        (
            Path(
                r"C:\Windows\Fonts\arial.ttf"
            ),
            Path(
                r"C:\Windows\Fonts\arialbd.ttf"
            ),
        ),
        (
            Path(
                r"C:\Windows\Fonts\calibri.ttf"
            ),
            Path(
                r"C:\Windows\Fonts\calibrib.ttf"
            ),
        ),
        (
            Path(
                "/usr/share/fonts/truetype/"
                "dejavu/DejaVuSans.ttf"
            ),
            Path(
                "/usr/share/fonts/truetype/"
                "dejavu/DejaVuSans-Bold.ttf"
            ),
        ),
        (
            Path(
                "/usr/share/fonts/truetype/"
                "liberation2/LiberationSans-Regular.ttf"
            ),
            Path(
                "/usr/share/fonts/truetype/"
                "liberation2/LiberationSans-Bold.ttf"
            ),
        ),
    ]

    for regular, bold in candidates:
        if (
            regular.exists()
            and bold.exists()
        ):
            return (
                str(regular),
                str(bold),
            )

    raise RuntimeError(
        "No suitable Unicode PDF font found"
    )


def register_pdf_fonts() -> None:
    if (
        "DwhReportRegular"
        in pdfmetrics.getRegisteredFontNames()
    ):
        return

    regular, bold = find_pdf_fonts()

    pdfmetrics.registerFont(
        TTFont(
            "DwhReportRegular",
            regular,
        )
    )

    pdfmetrics.registerFont(
        TTFont(
            "DwhReportBold",
            bold,
        )
    )


def build_pdf(
    *,
    report_title: str,
    columns: list[ExportColumn],
    rows: list[dict[str, Any]],
    generated_at: datetime,
) -> bytes:
    register_pdf_fonts()

    output = BytesIO()

    page_size = (
        landscape(A4)
        if len(columns) >= 4
        else A4
    )

    document = SimpleDocTemplate(
        output,
        pagesize=page_size,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=report_title,
        author="DWH Reporting",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DwhTitle",
        parent=styles["Title"],
        fontName="DwhReportBold",
        fontSize=15,
        leading=18,
        alignment=TA_LEFT,
        textColor=colors.HexColor(
            "#0B2D49"
        ),
        spaceAfter=5 * mm,
    )

    meta_style = ParagraphStyle(
        "DwhMeta",
        parent=styles["Normal"],
        fontName="DwhReportRegular",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor(
            "#526B7C"
        ),
        spaceAfter=5 * mm,
    )

    cell_style = ParagraphStyle(
        "DwhCell",
        parent=styles["Normal"],
        fontName="DwhReportRegular",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor(
            "#172A38"
        ),
    )

    header_style = ParagraphStyle(
        "DwhHeader",
        parent=cell_style,
        fontName="DwhReportBold",
        textColor=colors.white,
    )

    story = [
        Paragraph(
            escape_pdf_text(
                report_title
            ),
            title_style,
        ),
        Paragraph(
            escape_pdf_text(
                (
                    "Generated: "
                    + generated_at.strftime(
                        "%Y-%m-%d %H:%M:%S %z"
                    )
                    + f" | Rows: {len(rows)}"
                )
            ),
            meta_style,
        ),
    ]

    table_data = [
        [
            Paragraph(
                escape_pdf_text(column.label),
                header_style,
            )
            for column in columns
        ]
    ]

    for row in rows:
        table_data.append(
            [
                Paragraph(
                    escape_pdf_text(
                        display_value(
                            row.get(column.key)
                        )
                    ),
                    cell_style,
                )
                for column in columns
            ]
        )

    available_width = (
        page_size[0]
        - document.leftMargin
        - document.rightMargin
    )

    weight_total = sum(
        column.width
        for column in columns
    )

    column_widths = [
        available_width
        * column.width
        / weight_total
        for column in columns
    ]

    table = PdfTable(
        table_data,
        colWidths=column_widths,
        repeatRows=1,
        hAlign="LEFT",
    )

    table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor(
                        "#0B2D49"
                    ),
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.white,
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (-1, 0),
                    "DwhReportBold",
                ),
                (
                    "FONTNAME",
                    (0, 1),
                    (-1, -1),
                    "DwhReportRegular",
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.4,
                    colors.HexColor(
                        "#B6C4CF"
                    ),
                ),
                (
                    "BACKGROUND",
                    (0, 1),
                    (-1, -1),
                    colors.white,
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
            ]
        )
    )

    story.append(table)
    story.append(
        Spacer(
            1,
            2 * mm,
        )
    )

    document.build(
        story,
        onFirstPage=draw_pdf_footer,
        onLaterPages=draw_pdf_footer,
    )

    return output.getvalue()


def draw_pdf_footer(
    canvas,
    document,
) -> None:
    canvas.saveState()

    canvas.setFont(
        "DwhReportRegular",
        7,
    )

    canvas.setFillColor(
        colors.HexColor(
            "#637888"
        )
    )

    page_number = canvas.getPageNumber()

    canvas.drawRightString(
        document.pagesize[0]
        - document.rightMargin,
        7 * mm,
        f"Page {page_number}",
    )

    canvas.restoreState()


def escape_pdf_text(
    value: str,
) -> str:
    return (
        value
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )