中文 | [English](README.md)

# Slidea

Slidea 是一个基于 LangGraph 的幻灯片生成 skill，用于将高层次的演示需求转化为结构化研究材料、写作思路、幻灯片大纲，以及最终渲染产物。

它不是一个单纯的 PPT 导出脚本，而是一套分阶段的生成系统，具备缓存复用、可恢复执行、按页补渲染，以及可选的深度研究能力。

## Slidea 能做什么

给定一个类似“面向产品、技术与业务负责人，生成一份关于 AI Agent 的演示文稿”这样的请求，Slidea 可以：

- 将请求解析为结构化需求，
- 从用户输入、URL 与可选搜索中收集资料，
- 生成整份演示文稿的写作思路，
- 将写作思路转化为幻灯片大纲，
- 把每一页渲染为 HTML，
- 导出合并后的 PDF，并在可用时导出 PPTX。

这套系统面向 agent 驱动的使用场景，而不只是一次性生成。它支持分阶段执行、缓存复用。之所以采用分阶段设计，主要有两个原因：

- 研究、规划、大纲生成和渲染分离后，整体生成质量更稳定；
- 中间产物可以被缓存、检查、编辑、恢复或重复利用。

## 快速开始

### 推荐方式：使用 Agent 将 Slidea 安装为 Skill

Slidea 的主要定位是安装到 agent 环境中的 skill，而不是一个需要用户手工维护的独立 Python 项目。如果你的 agent 平台支持本地 skill，建议优先通过该平台的 skill 安装流程安装 Slidea，然后在 Slidea skill 目录中配置所需的 `.env`。

目前 Slidea Skill已适配 ARM openEuler，Apple Silicon macOS，Windows WSL/PowerShell，以及其他 Linux 系统。Slidea Skill 可无缝在主流 agent 环境中安装并运行，如 OpenClaw、Codex、Claude Code 等。

安装 Slidea skill，可将以下指令发送给你的 Agent：
```text
请直接获取并遵循这里的说明安装 slidea skill：https://gitcode.com/openeuler/capsule/tree/master/application/slidea/skill/INSTALL.md
```

安装完成后，重启 agent，使其重新加载已安装的 skill。随后通过你的 agent 环境支持的 skill 调用方式来触发 Slidea。

在 OpenClaw 这种环境中，你可能会这样调用：

```text
使用 slidea skill 创建一份关于 AI Agent 的 PPT，面向产品、技术与业务负责人
```

在 claude code 这种支持 slash 风格 skill 命令的环境中，你可能会这样调用：

```text
/slidea 创建一份关于 AI Agent 的 PPT，面向产品、技术与业务负责人
```

具体语法取决于宿主 agent，但预期体验是一致的：agent 加载 Slidea skill，在必要时补充缺失信息，并将幻灯片生成流水线执行到最终产物。

### 支持平台

| 平台 | 架构 | 支持情况 |
| --- | --- | --- |
| Windows | x86_64 / ARM64 | ✅ |
| macOS | Apple Silicon | ✅ |
| Linux | x86_64 | 仅支持 Ubuntu/Debian family |
| Linux | ARM64 | 仅支持 RHEL family |

### 从源码使用

如果你是为了贡献 Slidea 本身，或需要在本地调试仓库代码，可以直接从源码使用 Slidea。

1. 获取源码并进入目录：
   ```bash
   git clone https://gitcode.com/openeuler/capsule.git
   cd capsule/application/slidea
   ```

2. 使用脚本自动创建虚拟环境并安装相关依赖：
   这一步会自动处理 Python 依赖、Playwright 浏览器以及 LibreOffice 相关准备。
   ```bash
   python3 scripts/install/install.py
   ```

3. 配置环境变量：
   如果脚本还没有自动生成 `.env`，可以先执行：
   ```bash
   cp .env.example .env
   ```
   然后在 `.env` 中至少配置：
   - `DEFAULT_LLM_MODEL`
   - `DEFAULT_LLM_API_KEY`
   - `DEFAULT_LLM_API_BASE_URL`
   当前这三项仅支持 OpenAI-compatible API。

4. 运行示例：
   更多命令可以参考 `docs/cli.md`。
   ```bash
   .venv/bin/python scripts/run_ppt_pipeline.py \
     --text "<PPT 制作请求>" \
     --session-id test
   ```

如果你不想使用2中的安装脚本自动处理运行环境，也可以手动完成：
   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   python -m playwright install chromium
   ```
   LibreOffice 可以从这里下载并安装，再按系统方式配置环境：
   `https://www.libreoffice.org/download/download-libreoffice/`

## 仓库结构

- `scripts/`: 面向用户的 CLI 入口，包括 skill 导出、完整流水线、分阶段执行、补渲染以及嵌套的安装辅助脚本
- `skill/`: 导出的 skill 包定义目录，包含 `SKILL.md`、`INSTALL.md` 以及 skill 清单
- `core/`: 主要的 LangGraph 应用，包括深度研究、PPT 生成以及共享核心工具
- `docs/`: 面向公开仓库读者的文档，包括快速开始、CLI、架构和 app 说明
- `tests/`: 针对可移植性、CLI 契约与运行时行为的回归测试

## 核心子系统

### Deep Research

`core/deep_research/` 负责递归研究和长文综合。

它不负责幻灯片渲染，而是将一个宽泛的需求扩展成结构化研究过程，包括问题拆解、证据收集、缺口审视，以及生成可供演示流水线消费的研究产出。

当任务在进入幻灯片规划前需要先形成洞察时，应关注这一部分。

### PPT Generator

`core/ppt_generator/` 负责面向演示文稿的生成。

它会把源材料转化为：

- 演示文稿的写作思路，
- 幻灯片大纲，
- 页级 HTML 渲染结果，
- 以及最终的 PDF / PPTX 产物。

这个子系统被拆分出来，是为了把“如何思考这份 deck”与“如何把 deck 渲染出来”这两类问题分开处理。

## CLI 概览

Slidea 主要暴露三个脚本入口：

- `scripts/install/install.py`: 初始化源码工作区或导出 skill 包的本地运行时依赖
- `scripts/export_skill.py`: 从源码树导出 skill 包
- `scripts/run_ppt_pipeline.py`: 主生成流水线，支持分阶段执行
- `scripts/patch_render_missing.py`: 对缺失页或指定页进行补渲染

完整参数说明和 JSON 返回契约请参考 [CLI Reference](docs/cli.md)。

## 恢复被中断的运行

主流水线 CLI 现已支持恢复被 LangGraph 中断的运行。

当 `scripts/run_ppt_pipeline.py` 返回 `stage: "input_required"` 时，上层调用方应：

1. 把问题或选项展示给用户，
2. 等待用户明确回复，
3. 使用相同的 `run_id`、`session_id` 和 `--resume` 再次调用 CLI。

示例：

```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --resume "面向产品与技术负责人" \
  --session-id local-demo \
  --run-id <run_id>
```

对于选择题交互，上游也可以传结构化恢复 payload。运行时会按 `selection -> answer -> text -> message` 的顺序宽松提取恢复值。

当前限制：`--resume` 只在完整图执行路径 `--stages all` 中生效；分阶段执行仍然以缓存文件驱动，不直接恢复 LangGraph interrupt。

## 输出与缓存

每次运行都由一个 `run_id` 标识。

缓存中间结果保存在：

- `output/<run_id>/`

常见文件包括：

- `run.json`
- `references/`
- `research/`
- `thought/thought.md`
- `outline/outline.json`
- `ppt.json`

这里有一个关键区分：

- `output/<run_id>/` 是运行缓存和元数据目录
- 最终渲染产物写入 `ppt.json` 记录的 render 目录

这种分离让系统可以在不重跑全流程的情况下，重新进入某一阶段或执行补渲染。

## 运行时降级行为

运行时是由配置驱动的。

当可选服务缺失时，系统会降级，而不是整体失败：

- 没有 Tavily 配置：跳过网页搜索
- embedding 被禁用或未配置：跳过基于 embedding 的排序
- 没有可用的 LibreOffice 转换：保留 HTML/PDF 输出，跳过 PPTX 转换
- 没有 VLM 配置：跳过基于 VLM 的图片评分与分发能力

这让项目可以在不同能力等级的本地或远程环境中运行。

## 文档导航

根据你的目标，可以从这里开始：

- [Documentation Index](docs/README.md)
- [Quickstart](docs/quickstart.md)
- [CLI Reference](docs/cli.md)
- [Architecture Overview](docs/architecture.md)
- [App Overview](docs/core/README.md)
- [Deep Research App](docs/core/deep-research.md)
- [PPT Generator App](docs/core/ppt-generator.md)

## 验证

可以用以下命令运行回归测试：

```bash
python3 -m unittest tests.test_image_config -v
python3 -m unittest tests.test_runtime_config -v
python3 -m unittest tests.test_preflight -v
python3 -m unittest tests.test_runtime_options -v
python3 -m unittest tests.test_portability_polish -v
python3 -m unittest tests.test_pipeline_contracts -v
python3 -m unittest tests.test_cli_stage_smoke -v
python3 -m unittest tests.test_patch_render_cli_smoke -v
```

## 贡献

以下方向的贡献价值最高：

- CLI 契约与运行时稳定性
- research graph 质量
- 大纲或渲染质量
- 可移植性与环境处理
- 对外公开文档

如果你修改了行为，请在同一个改动中同步更新 `docs/` 下对应文档。
