# DocMD — 双引擎核验 Markdown 工具

> 本项目已暂停维护，源码以当前状态公开，欢迎使用、审阅或二次开发。

DocMD 是一个将 PDF、图片、Word、Excel、PPT 转换为 Markdown 的桌面工具。
它以「主识别 + MinerU 独立复核」为硬性前提：任一引擎不可用、失败或返回空内容时，任务不会开始处理，也不会输出 Markdown。只有两套独立结果均成功返回后，才会进入字段分级、人工确认与最终 Markdown 导出。

本仓库公开完整源码；OCR 运行时、模型权重和用户文档均不随源码或主安装包分发。首次启用真实双引擎时，可在应用的“模型与环境”页面按需安装，避免普通安装占用数 GB 空间。

把 PDF / 图片 / Word / Excel / PPT 转成带 **A/B/C/D 字段级可信等级** 的 Markdown，
经 **AI 汇总提问 → 用户确认门控** 后生成最终 `final.md` 与审计 `evidence.json`。

本仓库是可运行 MVP：核心流水线（导入 → 预处理 → 识别 → 分级 → 确认门控 → 最终导出）完整可用。
识别部分默认使用「可复制文本提取」；扫描件和图片默认要求真实 PaddleOCR-VL，
未安装/未配置时扫描件/图片会明确失败并提示安装，绝不产出模拟或空结果。
「AI 汇总提问」与「用户真实确认」是 finalize 不可绕过的双门控；API 密钥走加密存储，不落明文 SQLite。

## 核心原则（源自需求）

- **双引擎前提**：主引擎与独立复核引擎都必须成功返回；第二引擎未运行、失败或空结果时，任务直接失败，不生成 Markdown 审阅稿，也不允许以 C/D 继续流转。
- **默认原则**：仅在双引擎均成功返回后，C/D 冲突字段才保留在审阅 Markdown；AI 读完整文档后先汇总提问；用户确认前禁止后续处理；用户确认后才生成可用最终文件。
- **确认前门控**：字段等级 C/D 且未被用户确认 → `finalize()` 一律拒绝；只有全部待确认项解决才能导出。
- **关键字段**（金额/日期/姓名/编号/合同条款/表格数字/公式）冲突时**不得**自动降级为 A/B。
- **成本控制**：可复制文本优先文本提取不调用视觉模型；同一文件+引擎+配置用内容哈希去重缓存。

## 一键运行

> 已打包可直接运行的版本：双击 `dist/DocMD/DocMD.exe` 即可启动（`--windowed` 无控制台）。

```bash
pip install -r requirements.txt        # 或 pip install -e .
python -m docmd.run                    # 打开桌面界面
python -m docmd.run --data-dir .\mydata
```

## 桌宠交互

启动后默认显示置顶小熊，而不是传统的主窗口：

- 直接将 PDF、图片、Word、Excel 或 PPT 拖到小熊身上，它会在转换期间播放工作动画；出现 C/D 字段时，双击小熊打开确认面板。
- 单击小熊会触发开心动画；双击小熊可打开完整管理面板（待确认、AI 与模型设置）。
- 仅保留小熊桌宠；不包含小女孩、童话场景、视频或其他彩蛋素材。

数据目录结构：

```
data/raw/           导入文件副本
data/preprocessed/  按页渲染的原图
data/enhanced/      低质量页的增强图
data/output/        review_required.md / *_final.md / *_evidence.json
data/cache/         识别结果哈希缓存
data/docmd.db       SQLite（任务/中间结构/确认日志/模型与AI配置）
```

## 测试

```bash
python -m pytest -q                     # 自动化测试
```

覆盖：状态机与确认门控、数据库持久化、导入、预处理、统一结构、分级 A/B/C/D、
流水线 End-to-End、AI 提问模块、UI 冒烟（离屏）。

## 打包为 Windows EXE

```bash
pip install pyinstaller
python scripts/build.py                 # 生成 dist/DocMD.exe（--windowed）
```

> 大型视觉模型权重与 EXE 分离：不把数 GB 模型或完整 OCR Python 运行时塞进安装包。
> “模型与环境”页可检测 GPU 和依赖，并可安装 PaddleOCR-VL / MinerU 依赖。
> 模型权重与 EXE 分离，首次真实识别时按官方 SDK 下载；未装真实模型时扫描件会被拦截，不会回退 Mock。

## 架构

```
src/docmd/
  importer/   导入：类型识别、文本/视觉分流、确定性任务ID、副本落盘
  preprocess/ PDF 按页转图、质量检测（低清晰/阴影/裁切）、增强图
  extractors/ 识别引擎：text（PDF/Word/Excel/PPT 原生文本）+
              vision（PaddleOCR-VL / MinerU / DeepSeek 统一适配器；Mock 仅测试）
  core/       统一结构层 Document/ContentBlock/字段、归一化、常量、哈希缓存
  grading/    字段级可信度 A/B/C/D 分级与关键字段门控
  pipeline.py 流程编排：导入→预处理→识别→分级→待确认→确认→finalize
  ai/         AI 阅读提问（OpenAI 兼容 /chat/completions + manual 兜底）
  storage/    SQLite 持久化 + 确认日志 + 模型/AI 配置
  ui/         PySide6 桌面界面（任务/确认/AI设置 三标签页）
```

## 关键 API

```python
from docmd.pipeline import Pipeline
p = Pipeline("data_dir", vision_engine="mock")   # mock 仅用于演示；生产用 paddle_ocr

p.import_files(["发票.pdf"])            # 登记任务
tid = ...[0]["task_id"]
doc = p.process_one(tid)                # → 分级结束,status=awaiting_confirmation
doc.has_pending()                       # 是否有待确认字段（门控）

from docmd.ai import AiAsker
p.ai_question(tid, AiAsker(db=p.db), mode="auto")   # AI 完整阅读并汇总提问（持久化门控）

for f in doc.pending_fields():          # 用户真实确认（显式值，来源 candidate_a/b/manual）
    p.confirm_field(tid, f.id, f.candidates[0].text, "candidate_a")

p.finalize(tid)               # 双门控：必须【AI 已提问】+【无未确认字段】，否则抛 RuntimeError
# 产出 data/output/{tid}_final.md 与 {tid}_evidence.json，状态 → done
```

## 已知限制（当前版本）

- 真实 OCR 默认使用 PaddleOCR-VL 完整文档解析管线；MinerU 和 DeepSeek-OCR 作为 C/D 页复核引擎，需分别安装或配置。
- 预处理质量为规则式启发：倾斜/水印/透视仅标记候选，未做旋转校正的完整实现。
- AI 确认面板以「待确认清单 + 字段详情」呈现，未逐格显示原图截图（记录已保留来源原图路径）。
- MinerU / DeepSeek-OCR 适配器保留接口，未配置时为明确不可用、不伪装。
- 模型权重首次下载需要网络和足够的本机磁盘/内存；MinerU/DeepSeek-OCR 的自动复核策略需在模型配置后启用。

## 需求文档映射

仓库根目录 `docs/高可信_requirements.md` 为原始需求（若放置）；
当前实现覆盖 §1–§14 的核心可落地部分，§15 验收流程以 `tests/` 体现，§16 打包以
`scripts/build.py` 体现（模型分离与首启引导为待补）。
