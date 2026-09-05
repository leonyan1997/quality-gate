"""complexity 工具函数测试（extract_function_name / extract_complexity）"""

from quality_gate.checkers.complexity import (
    extract_complexity,
    extract_function_name,
)


class TestExtractFunctionName:
    def test_backtick_format(self):
        """clippy: 'cyclomatic complexity of `run_pipeline` (12)'"""
        assert extract_function_name(
            "cyclomatic complexity of `run_pipeline` (12)"
        ) == "run_pipeline"

    def test_the_function_format(self):
        """clippy 变体: 'the function `process_item` has a cyclomatic complexity of 15'"""
        assert extract_function_name(
            "the function `process_item` has a cyclomatic complexity of 15"
        ) == "process_item"

    def test_no_backtick_returns_empty(self):
        assert extract_function_name("no function mentioned here") == ""

    def test_empty_message(self):
        assert extract_function_name("") == ""


class TestExtractComplexity:
    def test_parenthesized(self):
        assert extract_complexity("complexity of `f` (12)") == 12

    def test_word_format(self):
        assert extract_complexity("complexity of 15") == 15

    def test_no_number(self):
        assert extract_complexity("nothing here") is None
