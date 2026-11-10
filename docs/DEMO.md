# M5/M6 演示页（现场展示用）

把一次真实 M5 运行的库变成可点击的页面：看每条译文当时实际收到的上下文、回滚到旧版本、人工锁定、改一条术语看 M6 只复查哪些行、以及各判定与调用次数。

## 启动

```bash
# 只预演：不接模型，术语变更只出报告、不重译
PYTHONPATH=src python3 scripts/serve_demo.py --db outputs/local_qwen/graph7.sqlite --port 7000

# 可执行：与 run 用同一份配置，页面才会出现“执行”按钮
PYTHONPATH=src python3 scripts/serve_demo.py --db outputs/local_qwen/graph7.sqlite \
    --config configs/local.qwen9b.maintenance.json --port 7000
```

- `--db` 可以是任意 `scripts/run_agent_workflow.py` 跑出来的库；`--run` 不填时取库里唯一的 run。
- 需要真实重译时，先按 [SETUP](SETUP.md) 起 llama-server 并设置 `LOCAL_LLM_KEY`，本地 9B 慢且会截断，演示建议先预演。
- 不需要浏览器以外的依赖：页面由标准库 `http.server` 提供。

## 页面四块

| 区域 | 内容 |
| --- | --- |
| 译文记录 | 原文、text_type、评审回执、版本、锁定标记 |
| 当前译文与版本链 | 每版译文与状态；“回滚到此版”追加新版本，历史不丢；“人工锁定”让 M6 判 locked |
| 模型实际收到的上下文 | 该版译文 context_manifest 里的条目原样列出，检索到但未供给的项不混进来 |
| 术语变更 | 填新译法 → 预演/执行 → 候选数、需修订、未改动、待人工、模型调用，逐条发现方式与 diff |

## 60 秒演示脚本

1. 点一条 v2 记录：讲版本链和评审回执 → 回滚到 v1，页面追加 v3，旧版本仍在。
2. 锁定这条 → 改一条术语并预演：该行判 `locked`，模型调用 0 次。
3. 解锁，换一条记录 → 预演“Scathelocke's Key → 斯卡瑟洛克之钥”：只命中 1 条，判 `unresolved`，调用 0 次；点“执行”才真实重译并追加新版本。
4. 点“重置演示数据”回到演示开始的状态，方便反复讲。

## 数据与限制

- 启动时把 `--db` 复制成 `outputs/demo/session.sqlite`，回滚、锁定、术语变更只写副本；“重置”重新复制，原库不变。
- 预演会先提交术语的新版本（这是真实的人工改动），但不改写任何译文；同一译法重复预演复用该版本，不叠版本。
- 已失效的术语不列出：它的证据已随变更过期，改译法属于重建，不是 `supersede`。
- 页面展示的是流程、判定和调用次数，不是译文质量结论；样例数据来自本地模型演示运行，不能当作真实实验质量结果。
- 完整维护流程（plan/apply/rollback/lock/export）仍用 `scripts/run_maintenance.py`，命令见 [INTERFACES](INTERFACES.md#m5-翻译闭环与-m6-更新流程2026-11-04)。
