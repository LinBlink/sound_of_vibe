# Sound of Vibe

给 Codex / Kimi CLI 添加实时中文、英文进度旁白。Recognizes Chinese and English progress and switches voices automatically.

```text
CLI JSONL → event adapter → bilingual progress rules → language / deduplication
          → latest-progress queue → Edge TTS → audio playback
```

首版是 Windows 优先的单次任务包装器。使用规则识别已有进度文字，并根据工具动作生成简短旁白，不额外调用总结或翻译模型。

## 安装 / Installation

需要 Python 3.11+，以及已安装、登录的 Kimi Code 2.x 或 Codex CLI。在项目目录用 PowerShell 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

当前工作区已经创建 `.venv` 并安装依赖，可以直接使用下列命令，无需激活虚拟环境。其他机器若只有 `py` 启动器，可将第一行改为 `py -3 -m venv .venv`。

## 使用 / Usage

```powershell
.\.venv\Scripts\sound-of-vibe.exe run kimi --prompt "检查项目结构"
.\.venv\Scripts\sound-of-vibe.exe run codex --prompt "Inspect the project structure"

# 指定目标目录 / Set the working directory
.\.venv\Scripts\sound-of-vibe.exe run kimi --cwd C:\path\to\project --prompt "查看 README"

# 只显示旁白文字；仍会执行 CLI 任务 / Disable audio, still run the task
.\.venv\Scripts\sound-of-vibe.exe run kimi --prompt "Inspect README.md" --text-only

# 包装器参数放在 -- 前；CLI 参数放在 -- 后 / Pass through CLI flags
.\.venv\Scripts\sound-of-vibe.exe run codex --prompt "检查项目" -- --sandbox read-only

# 固定英文旁白；不翻译中文句子，中文动作通过英文工具模板播报
.\.venv\Scripts\sound-of-vibe.exe run kimi --prompt "检查项目" --language en
```

原始 CLI stdout（JSONL）和 stderr 会继续显示；旁白输出到 stderr，带 `[voice/zh]` 或 `[voice/en]` 前缀。

| 参数 | 含义 / Meaning | 默认值 |
|---|---|---|
| `--language` | `auto`、`zh`、`en`；跟随内容或指定旁白语言 | `auto` |
| `--voice-zh` | 中文音色 / Chinese voice | `zh-CN-XiaoxiaoNeural` |
| `--voice-en` | 英文音色 / English voice | `en-US-AriaNeural` |
| `--rate` | 带正负号的语速百分比；负值写成 `--rate=-10%` | `+10%` |
| `--text-only` | 禁用音频和在线 TTS 请求 / Text only | 关闭 |
| `--cwd` | CLI 的工作目录（仅 `run`） | 当前目录 |
| `--executable` | CLI 可执行文件路径（仅 `run`） | 从 PATH 查找 |
| `--prompt` | 任务提示词；`run` 必填，`replay` 用于初始语言判断 | — |

包装器固定 JSONL 输出并管理 prompt，禁止透传覆盖 `--json`、`--output-format`、`--prompt` 等参数。Windows 上可直接运行原生 `.exe`；npm 安装的标准 Codex `.cmd` 启动器会通过 Node 入口执行，避免将任务文本拼接进 shell。

### 语言和过滤规则 / Language and filtering

- 中文识别“我先查看……”“正在修改……”“接下来验证……”；英文识别 “I'll inspect…”、“I'm updating…”、“Next, I'll test…” 等明确动作句。
- `auto` 逐句选择语言：去掉路径、行内代码和 URL 后含汉字则中文，否则含英文字母则英文。中文夹带 API / Java 等术语仍使用中文音色。
- 工具模板跟随最近一条已识别进度的语言；没有进度时跟随 prompt，仍无法判断则中文。不同语言的完整句子分别识别。
- `--language zh/en` 只接纳该语言的进度原文；另一语言通过工具模板播报，不调用翻译服务。
- 丢弃代码块、缩进代码、diff、堆栈、常见日志行、工具输出、推理内容和最后一条最终回答。不支持获取模型的私有推理。
- Kimi 和旧版 Codex 事件没有明确的最终回答标记时，将未分类的消息暂存，后续工具活动证明它是中间进度后才提取；末尾暂存消息丢弃。这可能略微延迟旁白。
- 每条最多一句，中文不超过 60 字、英文不超过 30 词。同一动作和语言 30 秒内去重，普通语音开始至少间隔 3 秒。
- 队列只保留当前一句和最新待播一句，快速进度会合并；新进度到来后，尚未播放的旧合成结果会被丢弃。已经播放的句子播放完毕再播最新进度。

工具模板覆盖读取、搜索、修改、测试、执行命令和通用工具；命令按可执行程序及参数白名单分类，`echo "pytest"` 不会被误判成运行测试。

### 运行边界

Kimi 使用 `--prompt --output-format stream-json`；此模式按 Kimi 自身规则自动执行工具，不弹出人工审批。Codex 使用 `exec --json`，保留其权限配置；需要修改项目时可显式透传 `--sandbox workspace-write`。不自动添加绕过权限的参数。

包装器复用 CLI 自己的登录配置，不读取或复制凭据。CLI 自身可能在用户目录写入会话数据。当前 Codex 文档要求 Git 工作目录，本项目的 Git 元数据目录已存在；其他目录请遵守 Codex 的目录检查。

Edge TTS 是在线服务，**只发送过滤后的旁白文字**，因此直接采用的进度句仍可能含项目名称。`--text-only` 完全关闭 TTS；不要把它误当成不执行 CLI 的 dry-run。

网络合成超时为 10 秒，最多重试一次；音色验证或音频设备初始化失败时降级为文字。合成失败不会阻塞 CLI 输出。正常退出保留 CLI 退出码；包装器启动错误返回 2，Ctrl+C 返回 130，并停止播放、终止其启动的进程树、清理临时音频。

## 不调用模型的演示 / Offline agent replay

以下命令不启动 CLI、不调用模型、不执行工具，使用仓库中的合成事件示例：

```powershell
.\.venv\Scripts\sound-of-vibe.exe replay kimi --file examples/kimi-bilingual.jsonl --text-only
.\.venv\Scripts\sound-of-vibe.exe replay codex --file examples/codex-bilingual.jsonl --text-only
.\.venv\Scripts\sound-of-vibe.exe replay codex --file examples/codex-bilingual.jsonl --text-only --failed
```

`replay` 去掉 `--text-only` 即启用在线 TTS。示例事件瞬间到达，语音队列通常会合并为最后的终态；要逐一确认两个音色的合成和播放，可运行：

```powershell
.\.venv\Scripts\python.exe tools/smoke_audio.py
```

## 验证 / Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

测试不调用模型或网络 TTS，覆盖双语识别、语言切换、噪声过滤、最终回答排除、JSONL 分块、语音队列、合成超时及清理、子进程日志排空、退出码和取消。实际双语合成与播放另由 `tools/smoke_audio.py` 验证。

Windows 使用 Job Object 管理本次启动的进程树，避免依赖全系统进程枚举；取消和正常结束都会清理本次启动的后代进程，防止继承输出管道的后台进程阻止退出。

当前环境验证（2026-10-04）：32 项自动化测试通过，两个默认音色在线查询成功，中文和英文短句实际合成并完成播放。解除早期沙箱限制后，Kimi Code 2.1.1 和 Codex CLI 0.160.0 的真实只读任务均已通过：分别识别英文、中文进度，排除工具内容和最终回答，退出码为 0；启用音频的真实 CLI 联调也正常结束，没有触发文字降级。

本机 Codex 位于 `C:\Users\wangb\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe`，Kimi 位于 `C:\Users\wangb\.kimi-code\bin\kimi.exe`，当前均可从 PATH 调用。曾经的“未找到 Codex”仅代表当时进程的 PATH 查询结果，并不代表未安装。

## Git 阶段提交

实现初期 `.git` 为只读，各阶段暂存为 `checkpoints/*.patch`（不纳入版本控制）。权限恢复后，6 个阶段补丁已依次导入为正式 Git commit；真实 CLI 联调发现的 PowerShell 命令分类改进也已单独提交。

后续开发在每个经过验证的阶段正常 `git add` / `git commit`。`tools/checkpoint.py` 保留为元数据只读时的备选导出工具；`tools/commit_checkpoints.ps1` 仅适用于尚无提交、索引为空的原始仓库，当前仓库已有提交，无需再次运行。脚本检测到已有历史或暂存内容时会停止，避免覆盖现有工作。

## 接口参考

- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Kimi Code CLI command](https://moonshotai.github.io/kimi-code/en/reference/kimi-command.html)
- [edge-tts](https://github.com/rany2/edge-tts)
- [pygame music playback](https://www.pygame.org/docs/ref/music.html)
