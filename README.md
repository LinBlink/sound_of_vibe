# Sound of Vibe

给 Codex / Kimi CLI 添加实时中文、英文进度旁白。Recognizes Chinese and English progress and switches voices automatically.

```text
CLI JSONL → event adapter → bilingual progress rules → language / deduplication
          → latest-progress queue → Edge TTS → audio playback
```

支持 Codex / Kimi 全局交互旁白和单次任务包装器，Windows 优先。通过规则或工具动作生成简短旁白，不额外调用总结或翻译模型。

## 安装 / Installation

需要 Python 3.11+，以及已安装、登录的 Kimi Code 2.x 或 Codex CLI。在项目目录用 PowerShell 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

当前工作区已经创建 `.venv` 并安装依赖，可以直接使用下列命令，无需激活虚拟环境。其他机器若只有 `py` 启动器，可将第一行改为 `py -3 -m venv .venv`。

## 使用 / Usage

### 直接运行 Kimi，所有项目自动播放 / Global interactive Kimi

本机已经全局开启。**在任意项目目录直接运行 `kimi`，输入任务即可自动播放**。已经打开的旧会话请退出后重新启动；首次输入任务创建会话时才会开始旁白。

```powershell
cd C:\path\to\project
kimi
```

在其他机器安装依赖后，先在本仓库目录执行一次安装：

```powershell
.\.venv\Scripts\sound-of-vibe.exe hooks install
.\.venv\Scripts\sound-of-vibe.exe hooks status

# 关闭全局旁白 / Disable global narration
.\.venv\Scripts\sound-of-vibe.exe hooks disable

# 重新开启 / Re-enable
.\.venv\Scripts\sound-of-vibe.exe hooks install
```

Run `hooks install` once, then use plain `kimi` in any project. Chinese prompts select Chinese narration; English prompts select English narration. Restart existing Kimi sessions after installation.

安装会备份 `~/.kimi-code/config.toml`，仅添加带标记的 Hooks 配置，保留其他配置和已有 Hooks；关闭时仅移除本工具的配置块。原生交互界面、工具权限和审批由 Kimi 自己处理。挂钩静默返回，后台进程合成、播放音频，空闲 120 秒后退出，下次任务自动启动。

**全局模式会朗读工具调用之间助手实际说的进展说明**，包括“修复后……”“54 项测试通过……”“接下来验证……”这样的整段对话，中文和英文分别使用对应音色。代码 diff、工具输出、报错堆栈、推理内容和最终回答继续过滤。

后台进程从当前 Kimi 会话的 `agents/main/wire.jsonl` 增量读取可见文字，在后续工具调用确认它属于执行过程后播报；从安装后的当前会话游标开始，不重播历史记录。支持已验证的 Kimi Code 2.1.1 Wire 记录格式，遇到未知格式时跳过原文、保留工具动作提示。

实际说明优先于排队的通用工具提示。没有说明时，仍播报任务开始、工具动作、工具结束或失败、审批等待及任务完成。连续 12 秒没有新阶段时，仅在日志记录仍在处理或等待工具结果；等待提示不播放人声，审批请求只响一次提问提示音；结束、取消和关闭会话后停止定时提示。

同类工具动作 5 秒内去重，实际说明按原文去重，不因属于相同动作而丢弃其他句子。语音开始间隔至少 1 秒。保留当前句及最多 6 条待播旁白，新进度不会取消正在合成的句子，完成叮咚排在待播执行动作后；超过队列上限时合并较旧的待播更新。多个会话共享一个播放队列。工具调用前的播报表示即将执行，是否批准仍由 Kimi 决定。

The global mode reads actual intermediate assistant prose in Chinese and English, including findings and test results. It filters code, tool output, reasoning, and final answers. Tool/status templates remain a fallback, with silent status logging after 12 seconds without a new stage.

默认日志：`%LOCALAPPDATA%\SoundOfVibe\worker.log`，包含事件类型、会话标识、过滤后的旁白和播放结果，不复制原始提示词、工具命令或工具结果。在线 TTS 接收过滤后的助手说明或旁白模板，说明可能包含项目名称。Hooks 失败时静默跳过，不影响 Kimi 工作。

配置保存了本仓库虚拟环境的绝对路径；移动仓库或重建 `.venv` 后需重新执行 `hooks install`。上述管理命令从本仓库运行；在其他目录管理时，请使用 `sound-of-vibe.exe` 的完整路径。单次任务包装器会自动避免与全局旁白重复播放。

### 直接运行 Codex / Global interactive Codex

本机已经安装并在原生 Codex 界面信任了全局 Hooks。在任意项目直接运行 `codex` 即可自动播报，旧会话建议重新启动。另一台机器需要安装一次，并通过原生 `/hooks` 审阅、信任标记为 **Sound of Vibe: voice narration** 的 8 个 Hooks。

```powershell
.\.venv\Scripts\sound-of-vibe.exe hooks install --source codex
.\.venv\Scripts\sound-of-vibe.exe hooks status --source codex
.\.venv\Scripts\sound-of-vibe.exe hooks disable --source codex
```

Only owned handlers in `~/.codex/hooks.json` are managed. Other hooks and `config.toml` remain intact. Review and trust the 8 voice hooks once in native `/hooks`; then run plain `codex` in any project.

从当前 `~/.codex/sessions` 会话游标增量读取 `phase=commentary` 的实际说明；过滤最终回答、推理、工具输出和重复消息。日志位于 `%LOCALAPPDATA%\SoundOfVibe\Codex\worker.log`。完成后空闲 120 秒退出；活动任务 10 分钟没有任何新事件时退出，下次 Hook 自动启动。Kimi 与 Codex 各自排队播放，音色分配跨两者协调。

### 音色选择与试听 / Voice selection and previews

在本仓库目录执行：

```powershell
.\.venv\Scripts\sound-of-vibe.exe voices
```

自动打开本地音色页：分别选择中文、英文音色，点击试听，再保存到 Kimi、Codex 或两者。也可以试听“完成”和“需要回答”两种叮咚。保持命令运行以使用页面，Ctrl+C 关闭服务；`--no-browser` 只打印访问地址。页面仅绑定 `127.0.0.1`，保存不修改 Hooks 命令，也不启用原本关闭的 Hooks。

Choose Chinese and English voices, preview, and save for Kimi, Codex, or both. The local page also previews both chimes. Keep the command running; Ctrl+C closes it. Voice previews use online TTS with fixed sample sentences.

默认开启 **每个工作会话自动分配不同音色**：以原生 Hook 的 session ID 为 Agent 身份，中英文各自保留一个音色，多轮任务保持稳定。独立包装器进程也参与同一个音色分配表。所选音色是新会话的首选，已占用时分配其他音色；现有会话保留音色。关闭自动分配则固定使用手选音色。这里识别的是独立 CLI 会话；没有独立会话事件的内部子 Agent 无法单独识别。

正常会话关闭且待播句子播放完毕后释放音色；异常退出留下的分配在 24 小时未使用后回收。可用音色数量有限，耗尽时保留文字并记录失败，不偷偷重复使用已占用音色。中文候选包含普通话、粤语、台湾及区域音色，自动分配可能改变口音。工作会话闲置超过 24 小时后恢复，可能重新分配。

人声默认使用本地固定音高重合成：男性 120 Hz、女性 190 Hz，统一音量峰值，不添加情感风格；试听与正式播放使用同一处理。目录与自动分配排除明显激情、活泼、可爱及 Expressive 等标签音色。默认首选新闻风格的 `zh-CN-YunyangNeural`、理性风格的 `en-US-EricNeural`，语速 `+0%`。处理会削弱中文声调，无法保证消除所有主观情绪感知。

任务结束等待输入时，播放完成叮咚；调用 `request_user_input` / AskUser 类工具、请求审批，或最终可见文字明确提出问题时，播放另一种叮咚。完成提示排在执行旁白后，同一回合不会被重复 Stop 事件重播。问题识别只读取可见文字，不播报最终答案。没有问号的隐含疑问不保证识别。

两份音效随包分发，可离线播放，不调用 TTS：完成音效来自 [Dub 的公有领域录音](https://commons.wikimedia.org/wiki/File:Doorbell-cheap-dingdong.ogg)，提问音效来自 [Amada44 的 CC0 录音](https://commons.wikimedia.org/wiki/File:Sound_Effect_-_Door_Bell.ogg)。来源记录在 `src/sound_of_vibe/assets/SOURCES.md`。

### 单次任务包装器 / Single-task wrapper

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
| `--voice-zh` | 中文音色 / Chinese voice | `zh-CN-YunyangNeural` |
| `--voice-en` | 英文音色 / English voice | `en-US-EricNeural` |
| `--rate` | 带正负号的语速百分比；负值写成 `--rate=-10%` | `+0%` |
| `--text-only` | 禁用音频和在线 TTS 请求 / Text only | 关闭 |
| `--cwd` | CLI 的工作目录（仅 `run`） | 当前目录 |
| `--executable` | CLI 可执行文件路径（仅 `run`） | 从 PATH 查找 |
| `--prompt` | 任务提示词；`run` 必填，`replay` 用于初始语言判断 | — |

包装器固定 JSONL 输出并管理 prompt，禁止透传覆盖 `--json`、`--output-format`、`--prompt` 等参数。Windows 上可直接运行原生 `.exe`；npm 安装的标准 Codex `.cmd` 启动器会通过 Node 入口执行，避免将任务文本拼接进 shell。

### 语言和过滤规则 / Language and filtering

- 已确认属于中间对话的助手文字会完整提取，包括发现、已完成的修复和测试结果；没有明确消息阶段时，先等待后续工具活动证明它是中间进展。通用进度识别仍支持“我先查看……”和 “I'll inspect…” 等动作句。
- `auto` 逐句选择语言：去掉路径、行内代码和 URL 后含汉字则中文，否则含英文字母则英文。中文夹带 API / Java 等术语仍使用中文音色。
- 工具模板跟随最近一条已识别进度的语言；没有进度时跟随 prompt，仍无法判断则中文。不同语言的完整句子分别识别。
- `--language zh/en` 只接纳该语言的进度原文；另一语言通过工具模板播报，不调用翻译服务。
- 丢弃代码块、缩进代码、diff、堆栈、常见日志行、工具输出、推理内容和最后一条最终回答。不支持获取模型的私有推理。
- Kimi 和旧版 Codex 事件没有明确的最终回答标记时，将未分类的消息暂存，后续工具活动证明它是中间进度后才提取；末尾暂存消息丢弃。这可能略微延迟旁白。
- 实际助手说明按句分段，长句分成最多 90 字或 40 词的播放段，不直接截掉后半段。说明按原文去重；包装器的工具模板仍按动作和语言 30 秒去重。
- 包装器同样优先保留实际说明，保留当前句与最多 6 条待播更新，语音开始间隔至少 1 秒；完成提示排在执行旁白之后。

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

`replay` 去掉 `--text-only` 即启用在线 TTS。示例事件瞬间到达时，超过队列上限的较旧更新会合并；要逐一确认两个音色的合成和播放，可运行：

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

当前环境验证（2026-10-04）：81 项自动化测试通过，两个默认音色在线查询成功，中文和英文短句实际合成并完成播放。解除早期沙箱限制后，Kimi Code 2.1.1 和 Codex CLI 0.160.0 的真实只读任务均已通过：分别识别英文、中文进度，排除工具内容和最终回答，退出码为 0；启用音频的真实 CLI 联调也正常结束，没有触发文字降级。

全局 Hooks 另验证了配置安装、关闭和重新开启，以及在独立临时项目中直接启动原生交互式 `kimi`：同一会话连续执行英文、中文只读任务，两种语言的开始、读取、工具结果、完成提示音依次完成播放。18 秒只等待和打印的命令运行期间，此前验证了等待工具结果的提示；按当前偏好，这类提示现在只记录日志，随后正常退出。可选 Windows 联调脚本如下，会调用已登录的 CLI 模型并读取脚本创建的测试 README：

实际说明模式另外通过原生交互联调：英文和中文工具调用前的可见说明从 Wire 执行事件中提取，每句完成音频播放后才播放任务完成；工具结果和最终版本号均未进入对话播报。用户提供的“修复后……54 项测试通过……接下来验证……”完整段落另有自动化回归测试。

```powershell
.\.venv\Scripts\python.exe -m pip install pywinpty
.\.venv\Scripts\python.exe tools/smoke_kimi_interactive.py
# 另验证长工具调用期间的旁白 / Also check a long tool call
.\.venv\Scripts\python.exe tools/smoke_kimi_interactive.py --long-task
# 验证实际中间对话原文 / Verify visible assistant commentary
.\.venv\Scripts\python.exe tools/smoke_kimi_interactive.py --commentary
# 裸 Codex：双语说明、完成提示音及提问提示音 / Plain Codex + chimes
.\.venv\Scripts\python.exe tools/smoke_codex_interactive.py
```

本机 Codex 位于 `C:\Users\wangb\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe`，Kimi 位于 `C:\Users\wangb\.kimi-code\bin\kimi.exe`，当前均可从 PATH 调用。曾经的“未找到 Codex”仅代表当时进程的 PATH 查询结果，并不代表未安装。

## Git 阶段提交

实现初期 `.git` 为只读，各阶段暂存为 `checkpoints/*.patch`（不纳入版本控制）。权限恢复后，6 个阶段补丁已依次导入为正式 Git commit；真实 CLI 联调发现的 PowerShell 命令分类改进也已单独提交。

后续开发在每个经过验证的阶段正常 `git add` / `git commit`。`tools/checkpoint.py` 保留为元数据只读时的备选导出工具；`tools/commit_checkpoints.ps1` 仅适用于尚无提交、索引为空的原始仓库，当前仓库已有提交，无需再次运行。脚本检测到已有历史或暂存内容时会停止，避免覆盖现有工作。

## 接口参考

- [Codex Hooks](https://learn.chatgpt.com/docs/hooks)
- [Parselmouth pitch manipulation](https://parselmouth.readthedocs.io/en/stable/examples/pitch_manipulation.html)
- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Kimi Code CLI command](https://moonshotai.github.io/kimi-code/en/reference/kimi-command.html)
- [Kimi Code Hooks](https://moonshotai.github.io/kimi-code/en/customization/hooks)
- [edge-tts](https://github.com/rany2/edge-tts)
- [pygame music playback](https://www.pygame.org/docs/ref/music.html)
