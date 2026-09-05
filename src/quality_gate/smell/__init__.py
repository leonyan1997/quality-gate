"""smell 引擎——Python 结构坏味道规则集

8 条规则：方法过长 / 类过大 / 参数过长 / 死代码 / 死导入 /
switch 语句过多分支 / 数据类候选 / 惰性类。重复代码检测不在
本引擎内（由 duplication checker 的 jscpd 承担）。
"""

from .base import BaseRule
from .data_class import DataClassRule
from .dead_code import DeadCodeRule
from .dead_import import DeadImportRule
from .large_class import LargeClassRule
from .lazy_class import LazyClassRule
from .long_method import LongMethodRule
from .long_parameter_list import LongParameterListRule
from .switch_statements import SwitchStatementsRule
from .types import Finding, SmellConfig

ALL_RULES: list[type[BaseRule]] = [
    LongMethodRule,
    LargeClassRule,
    LongParameterListRule,
    DeadImportRule,
    DeadCodeRule,
    SwitchStatementsRule,
    DataClassRule,
    LazyClassRule,
]


def get_enabled_rules(config: SmellConfig) -> list[BaseRule]:
    enabled = []

    # 如果显式指定了启用规则，只启用那些
    if config.enabled_rules:
        rule_set = set(config.enabled_rules)
        for rule_cls in ALL_RULES:
            if rule_cls.rule_id in rule_set:
                enabled.append(rule_cls())
    else:
        # 默认启用所有规则
        enabled = [cls() for cls in ALL_RULES]

    # 排除禁用规则
    if config.disabled_rules:
        disabled_set = set(config.disabled_rules)
        enabled = [r for r in enabled if r.rule_id not in disabled_set]

    return enabled


__all__ = [
    "ALL_RULES",
    "BaseRule",
    "DataClassRule",
    "DeadCodeRule",
    "DeadImportRule",
    "Finding",
    "LargeClassRule",
    "LazyClassRule",
    "LongMethodRule",
    "LongParameterListRule",
    "SmellConfig",
    "SwitchStatementsRule",
    "get_enabled_rules",
]
