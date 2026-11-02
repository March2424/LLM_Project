# 无 GPU 阶段的训练数据准备

日期：2026-11-01。状态：CPU 分组划分与采样计划已实现；没有生成冷启动译文、teacher 轨迹或 SFT/DPO 标签，没有调用付费模型。

## 已实现入口

```bash
PYTHONPATH=src python scripts/prepare_training_data.py \
  --output outputs/cpu_preparation_2026-11-02_per_game
```

仅使用标准库，输出目录必须新建，不覆盖旧结果。输入来自 data/game_samples.json 和各游戏 provenance，只取所需源身份字段，不将后台参考正文传给模型。

| 输出 | 用途 |
| --- | --- |
| splits.jsonl | ID、资源组、划分、类型、参考可用状态与训练重复标记，不含参考正文 |
| sources.jsonl | 源侧四列加 game/split，不含参考译文 |
| offline/references.jsonl | 独立参考，仅供离线审核与评分，不可送入观察或在线记忆 |
| sequences.jsonl | 每游戏/划分一个持续stream，含一次warmup_ids及跨资源组target_ids |
| teacher_pilot.jsonl | 训练侧 128 个拟试采样目标，按游戏/类型/上下文可用性轮流取样 |
| pilot_sequences.jsonl | 上述目标的声明子序列及冷启动 ID，不必翻译整个游戏 |
| summary.json | 输入哈希、分布、最大分组、限制与未执行阶段 |

最新默认 Flare、像素地牢按资源组约 85/15 分 train/test，不设置 dev；原 dev 并入 train且保留原 test 分组，Naev 留作跨游戏测试，这是草案而非冻结金标准。相同资源的 occurrence 保留各自业务 ID，但分到同组；较长字面/数值模板相同的记录联通分组。短通用文本不将所有场景合并，若其在 train 出现，对应 dev/test 目标不进入评分候选。跨游戏的字面/模板训练重复也排除于留出评分候选。

规则不是语义近重复检测或完整任务线边界恢复。summary 保留巨大联通组供复核，不为凑比例拆散分组；Naev 长共享文本会连接多个文件，最终抽取评测组前需复核。本轮只计划训练侧 pilot，不全量翻译 Naev。

每游戏/划分最多10条冷启动，只发生一次；只有整个游戏划分较短时缩短。按原始源侧position保持处理顺序，不主张剧情顺序。pilot_sequences声明游戏内目标子集，保持相对顺序；资源组切换不重建记忆。

## 历史含 dev 草案（保留旧运行计数，不是当前配置）

| 游戏 | train | dev | test / external_test |
| --- | ---: | ---: | ---: |
| Flare | 3,014 | 691 | 711 |
| 像素地牢 | 3,762 | 948 | 897 |
| Naev | 0 | 0 | 36,572 |

seed=20261101 的 128 个 pilot 目标涉及 97 个序列，计划冷启动共 418 条。它们只是 ID 与顺序清单，不是已执行的 546 条翻译。参考状态仍为 upstream_unreviewed；非空非 fuzzy 不等于质量批准。证据需求与记忆成熟度需在真实状态生成后记录，不能宣称五类记忆覆盖已验证。

## 服务器环境与后续边界

工作目录 /root/autodl-tmp/LLM_Project，虚拟环境 .venv-cpu；GitHub clone 保留真实 commit，新增准备代码不代用户提交或推送。源库与模型留在数据盘，凭据不写仓库。

```bash
source .venv-cpu/bin/activate
PYTHONPATH=src python scripts/prepare_training_data.py \
  --output outputs/cpu_preparation_2026-11-01
```

使用已有三个 requirements 的 CPU 依赖，不在无卡的 2GB 实例中加载完整训练栈。现有 Qwen3-8B 权重只检查文件、索引和 tensor header，补齐元数据，不执行推理。依赖锁和实际服务器报告保存在 artifacts/cpu-preparation/，以报告为准。

观察新增 memory_kind=term/style/summary/stm/ltm，保留 category、ID/version 和同一份去重译例；该标签不是类别配额。

后续 Teacher 入口已实现 train 源 ID 白名单、该记录的既有源侧 context、真实冷启动与未审记忆写回、冻结状态、teacher 调用恢复和候选导出；源侧边界测试通过，服务器已有部分旧协议付费采样，新协议升级及费用保留见TEACHER_TRAJECTORIES.md；学生 DPO 必须等 SFT 完成，见 [TRAINING.md](TRAINING.md)。


## 最新无 dev 草案

用户取消开发集。新输出目录使用 outputs/cpu_preparation_2026-11-01_nodev，不覆盖旧结果。Flare train=3,705、test=711；像素地牢 train=4,710、test=897；Naev external_test=36,572。训练配置与固定第 2 轮模型规则见 [LLAMAFACTORY_SETUP.md](LLAMAFACTORY_SETUP.md)。

最新 Teacher 采样入口、训练源 ID 白名单与进度页见 [TEACHER_TRAJECTORIES.md](TEACHER_TRAJECTORIES.md)。无 dev 新计划为128目标、91序列、388条计划冷启动；旧含dev运行计数保留作历史。


## 当前持续记忆计划（2026-11-02）

outputs/cpu_preparation_2026-11-02_per_game保留全部原train/test/external_test ID分配，改变的是运行stream，不改变防泄漏分组。全量计划5个game/split stream；训练pilot仅2个（Flare与像素地牢），各10条冷启动，共20条＋128目标=148步。Flare为81目标，像素地牢47目标；Naev仍仅作external_test计划，不进入Teacher训练采样。旧91个资源组/388冷启动为历史错误记忆边界，不再用于新采样。
