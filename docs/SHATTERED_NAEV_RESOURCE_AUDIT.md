# 破碎的像素地牢与 Naev：全类型本地化资源核查

核查日期：2026-10-30。目标是确认整款游戏的文本资源获取与中文参考情况，尚未生成统一四列 CSV、建立运行记忆或进行翻译质量实验。

后续实施更新：破碎的像素地牢的四列 CSV 与独立参考已生成，详见 [接入说明](SHATTERED_DATA.md)。下文语言键统计保留为原核查记录，不代表运行时使用位置数量。

后续实施更新：Naev 也已完成生成资源和 gettext 清单重建，四列 CSV 与 missing／fuzzy 状态参考见 [Naev 接入说明](NAEV_DATA.md)。下文关于尚未生成的描述为本轮实现之前的核查状态。

## 结论

- **破碎的像素地牢适合优先接入**：游戏加载的全部 9 组语言资源和调用源码已取得。4,973 条非空英文资源中，4,972 条有非空简体中文，缺 1 条。原始定义、格式参数及调用位置可供后续提取原生上下文。
- **Naev 的全类型原始材料可获取，但尚未完成完整提取**：已取得源码、数据、翻译和独立资源仓库中的加载文字；官方提供 XML、Lua、源码、TOML、百科和生成装备的提取路线。其现有 POT 有 23,191 个源条目，中文 19,724 条非空且未标记 fuzzy（85.05%），2,323 条为空，1,144 条标记 fuzzy。不能视为全中文参考数据。
- 上述数字是**语言资源键／gettext 条目数**，不是 occurrence 数。后续 CSV 必须恢复不同源侧使用位置，不能按相同原文合并。

## 固定版本与本地证据

| 项目 | 固定提交 | 已获取范围 |
| --- | --- | --- |
| [Shattered Pixel Dungeon](https://github.com/00-Evan/shattered-pixel-dungeon) | `57a4e06a4caf162446d1c28caa7983f0493fecf0`，v4.0.2 | `core/src/main/assets/messages/`、核心 Java 源码和桌面端源码 |
| [Naev 镜像](https://github.com/naev/naev) | `123fdc1be96d350fdff04cd2f61e2c66c99badd5` | `dat/`、`src/`、`po/`、`utils/`、`docs/` |
| [Naev 官方资源](https://codeberg.org/naev/naev-assets-lossy) | `7dcc63f767d6657b737686722f868575fbf8e5c8` | 主仓库 assets 子模块指定的同一提交；下载 `gfx/loading/*.txt`，未下载音画资源 |

原始材料、核查脚本 `audit.py`、统计 `coverage.json` 和 5,699 个已获取文件的 SHA-256 清单 `file_manifest.json` 保存在本地忽略目录 `artifacts/game_text_audit_2026-10-30/`。使用只读浅克隆与稀疏检出，不需要安装或运行游戏。重算现有材料的统计：

```bash
python3 artifacts/game_text_audit_2026-10-30/audit.py
```

该命令依赖本地已下载材料，不是自动下载入口。Naev GitHub 仓库标明是 Codeberg 的镜像；本次结论绑定上述镜像提交，不宣称是 Codeberg 最新版本。

## 破碎的像素地牢

[`Messages.java`](https://github.com/00-Evan/shattered-pixel-dungeon/blob/57a4e06a4caf162446d1c28caa7983f0493fecf0/core/src/main/java/com/shatteredpixel/shatteredpixeldungeon/messages/Messages.java) 明确加载 actors、items、journal、levels、misc、plants、scenes、ui、windows 九组资源。英文使用无语言后缀的 `.properties`，简体中文使用 `_zh.properties`；`Languages.java` 登记简体中文语言。已逐键检查同版本文件。

| 语言资源组 | 非空英文 | 非空中文 | 缺参考 |
| --- | ---: | ---: | ---: |
| actors：角色、NPC、状态、天赋与能力等 | 1,608 | 1,608 | 0 |
| items：物品、装备和相关说明等 | 2,071 | 2,070 | 1 |
| journal：日志、故事记录等 | 178 | 178 | 0 |
| levels：关卡相关文字 | 241 | 241 | 0 |
| misc | 235 | 235 | 0 |
| plants | 67 | 67 | 0 |
| scenes | 148 | 148 | 0 |
| ui | 49 | 49 | 0 |
| windows：窗口和操作提示等 | 376 | 376 | 0 |
| 合计 | **4,973** | **4,972** | **1** |

此外有 3 个英文和中文都为空的基类／默认资源键，未当作缺译。唯一缺失参考为 `items.quest.escapecrystal.injure_confirm`；它在英文资源中有内容，中文文件没有该键，运行时可以回退英文，但回退不计为中文参考。

单独核对的键子集：天赋 346、护甲能力 159、`spells` 类别 201、NPC 类别 164，均有非空中文。这些是前缀／类别子集，不是互斥总量。游戏有 NPC 任务与日志叙事，不能将其描述成具有大量连续长篇主线对白的游戏。

当前确认的是本体已国际化语言资源的覆盖；未证明源码中不存在遗漏国际化的硬编码可见文字，也没有穷举动态拼接后的全部屏幕文本。后续接入需检查 Java 调用、格式参数、默认值和分支，恢复 occurrence 与 context。

## Naev

| 用户需要的类别 | 已核实的源入口 |
| --- | --- |
| UI 与系统提示 | `src/`、`dat/gui/`、公共 Lua 脚本，以及 gettext 调用 |
| 剧情、任务、对话和事件 | `dat/missions/`、`dat/events/`、`dat/scripts/` |
| 物品、装备及说明 | `dat/outfits/`、`dat/commodities/`，以及构建时生成的装备 |
| 技能／能力及说明 | `dat/scripts/bioship/skills.lua`、`dat/outfits/bioship/skills/`、`dat/outfits/flow/abilities/`；是生物船技能与能力装备，不等同于 Flare 的人物技能树 |
| 飞船、阵营、星球及世界观 | `dat/ships/`、`dat/factions/`、`dat/spob/`、`dat/ssys/` |
| 百科、教程与其他文字 | `dat/naevpedia/`、相关生成器、`dat/start.toml` 和资源仓库 `gfx/loading/*.txt` |

[`po/its/translation.its`](https://github.com/naev/naev/blob/123fdc1be96d350fdff04cd2f61e2c66c99badd5/po/its/translation.its) 明确指定装备／飞船名称、描述及其他 XML 可翻译字段；[`utils/update-po.sh`](https://github.com/naev/naev/blob/123fdc1be96d350fdff04cd2f61e2c66c99badd5/utils/update-po.sh) 和 `po/meson.build` 组织源码、Lua、XML、TOML、百科、credits 和生成装备的提取。因此只读取 missions 或单个 PO 文件不足以恢复全类型原生上下文。

中文 [`po/zh_Hans.po`](https://github.com/naev/naev/blob/123fdc1be96d350fdff04cd2f61e2c66c99badd5/po/zh_Hans.po) 与 `po/naev.pot` 按 `(msgctxt, msgid)` 对齐，复数条目检查简体中文 `msgstr[0]`；fuzzy 独立计数，不算可直接使用的参考。

| 现有 POT 按来源定位的子集 | 源条目 | 中文非空且非 fuzzy | 空白 | fuzzy |
| --- | ---: | ---: | ---: | ---: |
| 引擎源码 UI／系统文字 | 2,594 | 2,415 | 137 | 42 |
| 任务与事件 | 12,234 | 10,286 | 1,426 | 522 |
| 装备 | 1,214 | 784 | 228 | 202 |
| 飞船 | 364 | 288 | 61 | 15 |
| 生物船技能脚本 | 80 | 62 | 16 | 2 |
| 百科 | 2,032 | 1,570 | 183 | 279 |

同条目可以被多个来源引用，表中子集不可相加。非空、非 fuzzy 也不意味着已通过本项目人工质量审核。全部 23,191 个 POT 键在中文 PO 中都有对应项，差别在内容为空或待核验，而非键缺失。

**完整性剩余工作**：现有 POT 的生成日期是 2026-10-13，早于固定源码提交，不能把这个数量当成当前源码的全量清单。构建生成的生物船装备、衍生装备和百科并非全部以最终文件形态入库；模板、生成器与规则已取得，但本机未安装 gettext/xgettext、Meson 等构建工具，本次没有重新生成最终资源和最新 POT，也没有核验生成器的完整依赖。因此确认有完整接入路线，尚不能声称已得到完整、最新的四列产物。`assets` 为独立子模块，已核对指定提交并取得加载文字；其他外部构建依赖及动态文本仍需按最终导入范围处理。

## 后续接入约定

优先处理破碎的像素地牢，随后补齐 Naev 的生成流程。以本体固定快照为范围，不混入第三方模组或其他版本的中文。源侧仍输出 `id,source_text,text_type,context`，中文单独输出 references；翻译键、源路径、分支及调用位置用于后台 occurrence 定位，不能直接把键当作模型分组提示。未翻译／fuzzy 条目保留源侧记录，不能为了双语齐全而删除。

许可入口分别是 SPD 的 `LICENSE.txt` 和 Naev 的 `LICENSE`。Naev 对源码与 XML 等内容区分 GPL 和 CC BY-SA 等许可；正式发布处理后数据时随来源保留具体许可与署名，不把游戏文本改称项目原创。
