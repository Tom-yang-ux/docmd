"""DocMD 核心常量：任务状态、字段可信等级、内容块类型。"""
from __future__ import annotations

from enum import Enum


class TaskState(str, Enum):
    """单文档任务状态机。"""
    IMPORTED = "imported"                    # 已导入
    PREPROCESSING = "preprocessing"          # 预处理中
    RECOGNIZING = "recognizing"              # 识别中
    GRADING = "grading"                      # 分级中
    AWAITING_CONFIRM = "awaiting_confirmation"  # 等待确认
    CONFIRMED = "confirmed"                  # 已确认
    DONE = "done"                            # 已完成


# 合法状态流转（门控：用户确认前不得进入业务处理）
TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.IMPORTED: {TaskState.PREPROCESSING},
    TaskState.PREPROCESSING: {TaskState.RECOGNIZING},
    TaskState.RECOGNIZING: {TaskState.GRADING},
    # 无 C/D 字段的文档不需要确认，分级后可直接生成最终文件。
    TaskState.GRADING: {TaskState.AWAITING_CONFIRM, TaskState.CONFIRMED},
    TaskState.AWAITING_CONFIRM: {TaskState.CONFIRMED},
    TaskState.CONFIRMED: {TaskState.DONE},
}


class FieldGrade(str, Enum):
    """字段级可信等级。"""
    A = "A"   # 已确认或高度一致
    B = "B"   # 高可信，仅格式差异
    C = "C"   # 存在候选冲突，需要确认
    D = "D"   # 无法可靠识别


class BlockType(str, Enum):
    """统一文档结构层的内容块类型。"""
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    IMAGE = "image_caption"
    FORMULA = "formula"
    KEY_FIELD = "key_field"


class EngineName(str, Enum):
    """识别引擎标识。"""
    TEXT_EXTRACTOR = "text_extractor"   # 可复制文本直接提取
    PADDLE_OCR = "paddle_ocr"           # 主视觉解析
    MINERU = "mineru"                   # 版式/表格/公式复核
    DEEPSEEK_OCR = "deepseek_ocr"       # 第三识别（冲突仲裁）


# 关键字段类型：出现冲突时不得自动降级为 A/B
KEY_FIELD_TYPES = {
    "amount",       # 金额
    "date",         # 日期
    "person_name",  # 姓名
    "number_id",    # 编号
    "contract_term",# 合同条款
    "table_number", # 表格数字
    "formula",      # 公式
}

# 允许直接进入业务处理的文档状态（门控）
CONFIRMED_STATES = {TaskState.CONFIRMED, TaskState.DONE}


def can_transition(current: TaskState, target: TaskState) -> bool:
    """校验从 current 到 target 是否合法。"""
    return target in TRANSITIONS.get(current, set())
