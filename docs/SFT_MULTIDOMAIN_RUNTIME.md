# 多领域Teacher轨迹：本地采样与进度

> 2026-11-03：Teacher已按用户要求安全停止，新增1199条、混合3165条已导出；当前数量和结构见[数据停止快照](SFT_MULTIDOMAIN_DATA_SNAPSHOT.md)。不自动续采，混合SFT未启动。下文运行计划保留供参考。

> 预算修复：已保留1371步旧状态／760有效选择／341历史失败，后续预算改为2048、字符上限12000，并增加一次同状态纠正。学生schema不变，SFT长度起点8192、response-only mask保持一致；原地址已恢复运行。详情与验证边界见[失败处理及长度对齐](TEACHER_BUDGET_FIX.md)，下文1024／512输出参数是原阶段记录。

日期：2026-11-03。用户授权“对齐之后开始SFT数据提取”。当前只采样并导出选择示范，不训练模型，不做DPO。

## 完成的对齐

- [align_multidomain_graphs.py](../scripts/align_multidomain_graphs.py)逐项处理已检查的39个skill／quest节点：29个明确误分类实体及其相连边失效，保留8个小说能力和2个明确任务行动。原始图、模型响应和费用不改；新种子位于`outputs/multidomain_sft_2026-11-03/aligned_graphs_v1/`，有效实体994、有效边1720。逐项决定及引文见该目录`corrections.json`。
- `graph_alignment.json`登记原图／副本哈希，Teacher只复制新种子；原始快照留审计，运行使用过滤后的graph快照。此为局部纠错，整体仍属program_only，不是全量语义批准。
- 新闻文件／小说章节作为固定topic，共52个训练主题。身份来自源单元元数据，不依据行序推断剧情。单元主题绑定允许已有源图中唯一且有证据的speaker绑定，不为未知说话人造身份；旧游戏原生绑定的默认行为不变。
- 主题绑定覆盖所有2955条允许训练的源记录；实际计划处理2420条。摘要按本run已经生成的译例更新；角色笔记至少2条证据，不要求每单元都有角色笔记，也不设最终选择类别配额。
- 旧1966条只统一学生模板首句game→text，逐条确认output和input不变。原始数据文件不覆盖，副本与转换哈希位于新运行的`alignment/`；336条继承旧基线历史标记仍保留。

新增topic／speaker兼容及同单元摘要检查通过，连同原有自动建图和Teacher运行验证共18项离线检查通过。夹具不当作真实模型质量结果。

启动后已核对首批真实完整目标：Teacher与翻译阶段model_context一致，topic_id绑定生效，记忆更新完成且候选中实际出现summary／term／STM／LTM。存在超预算选择，沿用旧协议记录为selection失败、不翻译、不写回、不裁剪为伪标签；失败会使最终有效条数低于目标数，不能按1900全部成功报告。

## 运行范围与文件

工作目录：`/Users/march/Desktop/LLM Project/LLM_Project_main`。

运行目录：`outputs/teacher_multidomain_2026-11-03/`。

计划：`outputs/multidomain_sft_2026-11-03/teacher_plan_aligned_v1/`。

目标为新闻En→Zh600、新闻Zh→En600、GuoFeng Zh→En700，共1900个选择目标；另520条冷启动只生成记忆，不计入SFT标签。52个单元run各冷启动一次、跨单元隔离；当前两个单元并发，同单元严格先选择、再按所选上下文翻译、最后更新记忆。教师目标数不保证全部成为有效或语义合格标签。

沿用deepseek-flash、temperature0、非thinking，Teacher最多512输出tokens，翻译／记忆最多1536；池16、0–8选、1024软token。无货币上限，保留调用／重试尝试、费用和失败。不额外生成Teacher参考译文，不向在线链路提供离线参考。

入口[run_multidomain_teacher.py](../scripts/run_multidomain_teacher.py)调用现有监督与采样器，`caffeinate`维持本机运行。解释器及凭据沿用相邻旧项目的CPU环境／`.env.extraction`，只在环境中传递密钥，进度目录不保存密钥。源码与提示词的指纹和副本已冻结，运行中不修改src或prompts。

## 终端进度

```bash
cd '/Users/march/Desktop/LLM Project/LLM_Project_main'
python3 scripts/report_teacher_trajectories.py --watch 5
```

显示总处理进度、冷启动／目标分别进度、有效选择、失败、费用与当前任务，以及各类候选的真实曝光数。Ctrl+C仅退出监控。

查看日志：

```bash
tail -f outputs/teacher_multidomain_2026-11-03/console.log
```

## process.html

已启动仅绑定本机的HTTP服务：

[打开process.html](http://127.0.0.1:8765/process.html)

文件：`outputs/teacher_multidomain_2026-11-03/process.html`。与兼容保留的`progress.html`同步原子更新，每5秒刷新；可以查看当前及最近30条记录、理由、所选上下文、记忆状态和issues，点击源ID进入完整逐条JSON。

服务已通过HTTP200及自动刷新检查。重启查看服务可使用：

```bash
python3 -m http.server 8765 --bind 127.0.0.1 \
  --directory outputs/teacher_multidomain_2026-11-03
```

当前服务已经运行，不要在同端口重复启动。服务与采样独立，关闭页面不停止采样。

## 停止、断点和导出

```bash
touch outputs/teacher_multidomain_2026-11-03/STOP
```

当前条的选择／翻译／记忆更新结束后停止开始新条，不强制取消在途请求。逐条JSON、SQLite运行记忆、成功响应缓存和费用账本保留；中断未知请求不盲目重复付费。

采集结束且状态为complete／complete_with_warnings／complete_with_failures时，监督入口导出有效选择；摘要曝光检查保留，角色缺省不作为硬性失败门槛。失败数与失败来源不抹除，不复制样本凑1900。

新增数据位于运行目录`new_data/`；与旧对齐副本合并后的注册数据目录为`outputs/selector_training/multidomain_2026-11-03/`，含逐行provenance、seen标记、数量和哈希。训练未启动，完整真实模板长度／loss检查仍是未来训练前步骤；导出metadata标记`tokenization_validated=false`。

这些是运行及导出设计，最终完成与实际行数以progress、launcher、dataset-export和数据metadata为准，不把启动、程序通过或低失败率当翻译质量收益。
