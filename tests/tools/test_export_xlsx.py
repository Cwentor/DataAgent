"""xlsx 真导出（#28）测试：类型保真 / 公式防护 / 脱敏同源 / 超限报错 / 契约兼容。

设计文档：docs/designs/2026-11-02-issue28-xlsx-export-design.md
"""

from __future__ import annotations

import io
import sys
import threading

import pytest
from openpyxl import load_workbook

from tools.base import DisplayType, ToolContext, ToolResult
from tools.builtins import _export_store
from tools.builtins.export_report_tool import (
    _XLSX_MAX_ROWS,
    ExportReportArgs,
    _serialize,
    _serialize_xlsx,
    export_report_tool,
)


def _read_grid(content: bytes):
    """落盘产物读回校验（不信内存对象：write_only 落盘后读回断言）。"""
    wb = load_workbook(io.BytesIO(content))
    return [list(row) for row in wb.active.iter_rows()]


class TestTypeFidelity:
    """类型保真（设计文档 D3：值形态保守识别）。"""

    def test_type_fidelity_matrix(self):
        """核心矩阵：数字 / 超界整数 / 日期识别 / 前导零 / 布尔 / 空值 / 非有限浮点。"""
        columns = ["i", "bigint", "f", "d", "dt", "leading_zero", "b", "n", "inf_v", "txt"]
        rows = [
            [
                12345,
                12345678901234567890,
                3.14,
                "2025-12-31",
                "2025-12-31 10:30:00",
                "00123",
                True,
                None,
                float("inf"),
                "hello",
            ]
        ]
        content, ext = _serialize_xlsx(columns, rows)
        assert ext == "xlsx"
        grid = _read_grid(content)
        header, data = grid[0], grid[1]
        assert [cell.value for cell in header] == columns
        # int 数字格（2^53 内无损）
        assert data[0].value == 12345 and data[0].data_type == "n"
        # 超 2^53 整数写文本（double 精度边界外唯一保真方式）
        assert data[1].value == "12345678901234567890" and data[1].data_type == "s"
        assert data[2].value == 3.14 and data[2].data_type == "n"
        # 严格 ISO 日期字符串转日期格
        assert data[3].is_date and data[3].value.year == 2025
        assert data[4].is_date and data[4].value.hour == 10
        # 前导零文本保真
        assert data[5].value == "00123" and data[5].data_type == "s"
        assert data[6].data_type == "b"
        assert data[7].value is None
        # 非有限浮点写文本（Excel 无 inf/nan 语义，不静默改值）
        assert data[8].value == "inf" and data[8].data_type == "s"
        assert data[9].value == "hello" and data[9].data_type == "s"

    def test_bool_not_treated_as_int(self):
        """bool 是 int 子类，必须先判 bool（否则 True 落数字格 1）。"""
        content, _ = _serialize_xlsx(["b"], [[False]])
        cell = _read_grid(content)[1][0]
        assert cell.data_type == "b" and cell.value is False

    def test_iso_datetime_with_t_and_microseconds(self):
        """T 分隔与小数秒形态同样识别（Gmall 埋点时间形态）。"""
        content, _ = _serialize_xlsx(["t"], [["2025-12-31T10:30:00.5"]])
        cell = _read_grid(content)[1][0]
        assert cell.is_date and cell.value.microsecond == 500000

    def test_date_like_but_not_iso_stays_text(self):
        """相似但非严格 ISO 形态不得误转日期（保守识别边界）。"""
        for value in ["2025/12/31", "2025-12", "2025-12-31T10:30", "2025-13-01"]:
            content, _ = _serialize_xlsx(["v"], [[value]])
            cell = _read_grid(content)[1][0]
            assert cell.data_type == "s" and cell.value == value, value

    def test_timezone_aware_string_stays_text(self):
        """带时区字符串保持文本（不猜时区）。"""
        content, _ = _serialize_xlsx(["v"], [["2025-12-31 10:30:00+08:00"]])
        cell = _read_grid(content)[1][0]
        assert cell.data_type == "s"


class TestFormulaInjection:
    """公式注入防护（设计文档 D4：data_type='s' 强制文本）。"""

    @pytest.mark.parametrize("payload", ["=cmd()", "+1+cmd", "@SUM(A1)", "-2-3", "=SUM(A1)"])
    def test_formula_like_string_forced_text(self, payload):
        content, _ = _serialize_xlsx(["v"], [[payload]])
        cell = _read_grid(content)[1][0]
        assert cell.data_type == "s" and cell.value == payload

    def test_header_always_text(self):
        """表头恒为文本格（列名不做日期识别、不进公式推断）。"""
        content, _ = _serialize_xlsx(["=sum", "2025-12-31"], [[1, 2]])
        header = _read_grid(content)[0]
        assert all(cell.data_type == "s" for cell in header)
        assert [cell.value for cell in header] == ["=sum", "2025-12-31"]


class TestRowLimit:
    """行数上限双层语义（设计文档 D6）。"""

    def test_hard_limit_rejects_with_csv_hint(self):
        """格式硬限：超 1048576 行（含表头）显式报错并建议 csv，严禁截断。"""
        with pytest.raises(ValueError, match="csv"):
            _serialize_xlsx(["a"], [[1]] * _XLSX_MAX_ROWS)

    def test_user_limit_truncation_regression_xlsx(self, tmp_path, monkeypatch):
        """用户预算层回归：limit 截断语义在 xlsx 路径同样生效且诚实提示。"""
        store = _export_store.ExportStore(root=tmp_path / "exports")
        monkeypatch.setattr(_export_store, "_default_store", store)
        monkeypatch.setattr(_export_store, "_store_lock", threading.Lock())

        prior = ToolResult(
            success=True,
            data={
                "columns": ["order_id", "amount"],
                "rows": [[f"o{i}", float(i)] for i in range(6)],
                "row_count": 6,
            },
            display_type=DisplayType.TABLE,
        )
        result = export_report_tool.run(
            ExportReportArgs(format="xlsx", limit=5).model_dump(), ToolContext(prior=prior)
        )
        assert result.success
        data = result.data
        assert data["truncated"] is True and data["row_count"] == 5
        assert any("截断" in n for n in data["notes"])
        assert data["filename"].endswith(".xlsx")
        # 落盘产物行数与 row_count 一致（表头 + 5 行）
        item = store.get(data["export_id"])
        assert len(_read_grid(item.read_bytes())) == 6


class TestParityWithCsv:
    """脱敏与审计同源（issue 验收：与 CSV 路径完全一致，同源同测）。"""

    @staticmethod
    def _make_ctx() -> ToolContext:
        prior = ToolResult(
            success=True,
            data={
                "columns": ["order_id", "user_id", "phone", "amount"],
                "rows": [
                    ["o1", "u1", "13800000000", 100.0],
                    ["o2", "u2", "13900000000", 200.0],
                ],
                "row_count": 2,
            },
            display_type=DisplayType.TABLE,
        )
        return ToolContext(prior=prior)

    def test_mask_parity_csv_xlsx(self, tmp_path, monkeypatch):
        store = _export_store.ExportStore(root=tmp_path / "exports")
        monkeypatch.setattr(_export_store, "_default_store", store)
        monkeypatch.setattr(_export_store, "_store_lock", threading.Lock())

        results = {}
        for fmt in ("csv", "xlsx"):
            result = export_report_tool.run(
                ExportReportArgs(format=fmt).model_dump(), self._make_ctx()
            )
            assert result.success
            results[fmt] = result.data

        csv_data, xlsx_data = results["csv"], results["xlsx"]
        # 治理面同源：脱敏标记 / notes / 截断 / 预览完全一致（敏感列均掩码）
        assert csv_data["desensitized"] == xlsx_data["desensitized"] is True
        assert csv_data["notes"] == xlsx_data["notes"]
        assert csv_data["truncated"] == xlsx_data["truncated"] is False
        assert csv_data["preview"] == xlsx_data["preview"]
        assert all(row[1] == "***" and row[2] == "***" for row in xlsx_data["preview"])
        # xlsx 落盘物无明文敏感值
        item = store.get(xlsx_data["export_id"])
        values = [cell.value for row in _read_grid(item.read_bytes()) for cell in row]
        assert "u1" not in values and "13800000000" not in values
        assert "***" in values

    def test_openpyxl_missing_reports_honest_error(self, monkeypatch):
        """openpyxl 未安装时诚实报错并给安装指引（不静默降级为 CSV）。"""
        monkeypatch.setitem(sys.modules, "openpyxl", None)
        with pytest.raises(ValueError, match="pip install openpyxl"):
            _serialize_xlsx(["a"], [[1]])


class TestContractCompat:
    """契约兼容（issue 验收：excel 语义不变，xlsx 新枚举可用）。"""

    def test_excel_still_bom_csv(self):
        """excel 既有语义字节级回归：仍为 UTF-8 BOM 的 Excel 兼容 CSV。"""
        content, ext = _serialize("excel", ["a", "b"], [[1, "x"]])
        assert ext == "csv"
        assert content.startswith(b"\xef\xbb\xbf")

    def test_xlsx_enum_yields_xlsx_ext(self, tmp_path, monkeypatch):
        """xlsx 枚举走工具全链路：扩展名 / format 回显 / 下载链接齐备。"""
        store = _export_store.ExportStore(root=tmp_path / "exports")
        monkeypatch.setattr(_export_store, "_default_store", store)
        monkeypatch.setattr(_export_store, "_store_lock", threading.Lock())

        result = export_report_tool.run(
            ExportReportArgs(format="xlsx", filename="订单明细").model_dump(),
            ToolContext(prior=self._prior()),
        )
        assert result.success
        data = result.data
        assert data["format"] == "xlsx"
        assert data["filename"] == "订单明细.xlsx"
        assert data["download_url"].startswith("/api/export/")

    @staticmethod
    def _prior() -> ToolResult:
        return ToolResult(
            success=True,
            data={
                "columns": ["order_id", "amount"],
                "rows": [["o1", 100.0]],
                "row_count": 1,
            },
            display_type=DisplayType.TABLE,
        )
