Codex / Kimi
↓
实时输出监听
↓
过滤代码和垃圾日志
↓
只提取“正在做什么”
↓
Edge TTS 中文 / English 旁白

首版实现状态：
- [x] Codex exec JSONL / Kimi Code 2.x stream-json 适配
- [x] 实时读取 stdout / stderr，过滤代码、日志、工具结果和最终回答
- [x] 中文与英文动作句识别，双语工具模板
- [x] 自动选择语言，分别配置中文与英文音色
- [x] 去重、语音限流、最新进度队列
- [x] Edge TTS 异步合成、播放、超时重试和清理
- [x] CLI 包装器、无模型回放、使用文档
- [x] 自动化测试、实际中英文合成播放
- [x] 恢复 .git 写权限后导入 checkpoints 阶段提交
- [x] 真实 Kimi Code 2.1.1 任务：英文进度提取、只读工具、音频播放、退出码 0
- [x] 真实 Codex CLI 0.160.0 任务：中文进度提取、只读命令、音频播放、退出码 0
