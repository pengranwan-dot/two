# data_<YYYYMMDD> 数据分析工具使用手册

适用目录：按天存储的试验数据根目录（当前仓库有 `data_1012/`（嵌套 `data/` 旧布局）、
`data_1013/`、`data_1014/`（扁平布局））；工具本体位于项目根目录 `tools/`，下文以
`tools` 代指（可整体移动到任意位置，只要 `--root` / `DATA_ROOT` 指到数据即可）。
纯 Python 标准库（≥3.8）+ sqlite3 直读 bag，无需 rosbag2 / ROS 环境；不依赖 numpy。
所有脚本均支持 `-h/--help` 查看参数。

工具速查：

| 工具 | 一句话用途 |
|---|---|
| `list_tests.py` | 测试清单与数据完整性（bag/log/快照/note/人工测量） |
| `bag_info.py` | bag 话题、消息数、时长 |
| `extract_topics.py` | bag 数值话题 → `csv/<ts>/` 缓存（加速分析） |
| `parse_controller_log.py` | 控制器 log 事件时间线 / 周期行导出 |
| `test_summary.py` | 每日汇总：自动指标表 + 人工测量表 + 达成率 |
| `auto_summary.py` | 对位结果统计表（旧格式单表，可写入/替换文档段） |
| `test_report.py` | 单测试报告（阶段时刻/收敛/停车窗口/人工测量） |
| `params_table.py` | 参数与温度记录表 + 快照变化 |
| `phase2_stats.py` | 阶段二 ground height / ltg / chcnav 统计 |
| `recalc_initial.py` | 从 bag 感知复算初始偏差 |
| `config_diff.py` | 参数快照对比（含测试→快照映射） |
| `gen_roll_temp_comp.py` | 由多天数据生成 roll 分段温度补偿参数 |
| `gen_stats_template.py` | 按测试时间戳生成人工测量模板 `stats/stat-<日期>.md` |
| `stats_to_table.py` | 人工测量小节 → 表格 / CSV |
| `run_all.sh` | 一键批量运行全部分析脚本（含逐测试报告） |
| `fetch_remote_data.sh` | 从车端同步 logs/bags/configs |

---

## 1. 数据目录与命名约定

### 1.1 目录结构

```
data_<YYYYMMDD>/                 # 一天 = 一个数据根目录（root）
├── bags/autorecord_<YYYYMMDD_HHMMSS>/     rosbag2（*_0.db3 + metadata.yaml）
├── logs/                                  <节点名>_<YYYYMMDD_HHMMSS>.log
│     ├── motion_control_newdf_<ts>.log    控制器终端输出（核心分析对象）
│     ├── arc_detector_node_<ts>.log       感知节点输出
│     └── autorecord_<ts>.log               ros2 bag record 输出
├── configs/autonomous_config[-_]<YYYYMMDD_HHMMSS>/   参数快照
│     └── configs/newdf_dpt15/control/control_config.yaml
├── notes/note-<YYYYMMDD>.md               试验备注（按时间戳行；兼容 note-<MMDD>.md）
├── stats/stat-<YYYYMMDD>.md               人工测量统计（按 `## <时间戳>` 小节；兼容 stat-<MMDD>.md）
└── csv/<ts>/...                           extract_topics.py 导出的 CSV 缓存（建议 gitignore）
```

兼容说明：

- 也支持 `root/data/{bags,logs,configs,notes,stats}` 嵌套一层的旧整理方式（`data_1012` 即此布局）。
- 参数快照目录名 `autonomous_config-<ts>`（连字符）与 `autonomous_config_<ts>`（下划线）均可识别。
- **测试 id = 时间戳**（`YYYYMMDD_HHMMSS`）：bag / 三个 log / note / stats 按时间戳关联；
  某测试生效的参数快照 = 时间上不晚于它的最近一次快照（同时间戳快照优先视为本次）。
- log 中墙钟时间（`[HH:MM:SS.mmm]`）+ bag 首条消息日期 → epoch（`common.wall_to_epoch`）。

### 1.2 note 文件格式（试验备注）

```
20261013_145323 -- 20261013_153750 (roll_cmd_smooth_enable: false)   # 配置生效区间
20261013_150522 对位误差: 39                                          # 单次测试备注
车辆重启后驱动正常启动，但都 echo 不到数据……                            # 无时间戳行 = 当日整体记录
```

- 以时间戳开头的行 → 关联到该测试（`test_summary` 自动填入“备注”列）；
- 无时间戳行 → 作为当日“试验记录”汇总在表尾。

### 1.3 stats 文件格式（人工测量）

```markdown
## 20261013_142035

当前 `roll_cmd_smooth_enable: false`      # 可选：自由说明（会显示为备注）

- 车头偏左 -8
- lift 6 5
- 车头左右距离 143 148
- 时间 100
```

- 五个字段：车头偏左(mm)、lift 高度差左/右(mm)、车头左右距离左/右(mm)、时间(s)；
  只有五个字段齐全的测试才会进入对位统计表与达成率计算。
- 无测量的小节可只写 `有问题` 或一段说明——工具会将其列入“未列入（未测量/异常）”。

---

## 2. 数据根目录解析与运行方式

所有工具均支持 `--root` 指定数据根目录（含 `bags/` 或 `data/bags/` 的目录），解析优先级：

1. 命令行 `--root data_1013`（相对当前目录或绝对路径）
2. 环境变量 `DATA_ROOT=data_1013`
3. 当前目录、`tools` 目录或其上级目录本身是 `data` / `data_*` / `data-*` 目录
   （或直接含 `bags/`）时直接使用
4. 否则取当前目录 / `tools` 上级目录下**最新的 `data_*` / `data-*` 目录**（按名称中的日期排序；
   多候选时 stderr 打印 `[info] 自动选择…`）。仓库根下的普通 `data/` 不会被自动选中，
   需要时用 `--root data` 显式指定

```bash
# 在仓库根目录运行（tools/ 与 data_*/ 同级，推荐；示例用 data_1014）
python3 tools/test_summary.py --root data_1014      # 明确指定某天
python3 tools/test_summary.py                       # 缺省自动选最新 data_*

# 也可进入 tools 目录或某天数据目录运行
cd tools && python3 test_summary.py --root ../data_1014
cd data_1014 && python3 ../tools/test_summary.py    # 当前目录即数据根

# 只看某天时 export DATA_ROOT
export DATA_ROOT=data_1014
python3 tools/list_tests.py
```

### 2.1 输出约定（报告类工具）

报告类工具（`test_summary / auto_summary / test_report / params_table / phase2_stats /
config_diff`）**默认把结果写入数据根目录下**，无需手动 `--out`：

| 工具 | 默认输出 |
|---|---|
| `test_summary.py` | `<root>/reports/test_summary.md` |
| `auto_summary.py` | `<root>/reports/auto_summary.md` |
| `test_report.py <ts>` | `<root>/reports/test_report_<ts>.md` |
| `params_table.py` | `<root>/reports/params_table.md` |
| `phase2_stats.py` | `<root>/reports/phase2_stats.md` |
| `config_diff.py` | `<root>/reports/config_changes.md` |
| `gen_roll_temp_comp.py` | `<第一个 root>/reports/roll_temp_comp.md` |

- `--print`（或 `--out -`）只打印到终端不写文件；`--out PATH` 指定其它路径；
- `auto_summary.py` 重复运行会替换文件中的 `## 对位结果统计` 段（其余内容保留）；
- `parse_controller_log.py --cycles-csv` 不带值时默认导出到
  `<root>/csv/<ts>/controller_cycles.csv`（`-` 打印到终端，也可给路径）；
- `extract_topics.py` 始终写入 `<root>/csv/<ts>/...`。

---

## 3. 快速开始（一天数据完整流程）

**一键运行（推荐）**：指定数据根目录后自动依次执行全部分析脚本，报告汇总到
`<root>/reports/`：

```bash
bash tools/run_all.sh --root data_1014                      # 全量（含 CSV 缓存与逐测试报告）
bash tools/run_all.sh --root data_1014 --no-extract --no-reports   # 快速模式
bash tools/run_all.sh --root data_1014 --date 20261014 \
     --from 150000 --to 160000 --exclude 154155             # 过滤子集
```

也可逐条手动运行（下面 7 步与 `run_all.sh` 等价，便于按需调整）：

```bash
T=tools                                # 工具目录（项目根下）
R=data_1014                            # 数据根目录（当天，改成实际日期）

python3 $T/list_tests.py --root $R                   # 1) 数据完整性检查
python3 $T/extract_topics.py --root $R --all         # 2) 建 CSV 缓存（可选，加速后续分析）
python3 $T/test_summary.py --root $R                 # 3) 自动指标 + 人工测量 + 达成率 → reports/test_summary.md
python3 $T/auto_summary.py --root $R                 # 4) 对位结果统计表          → reports/auto_summary.md
python3 $T/params_table.py --root $R                 # 5) 参数与温度表            → reports/params_table.md
python3 $T/phase2_stats.py --root $R                 # 6) 阶段二/ltg 统计         → reports/phase2_stats.md
python3 $T/config_diff.py --root $R --assoc          # 7) 参数变更追溯            → reports/config_changes.md
```

报告默认写入 `$R/reports/`（见 2.1）；只想在终端看加 `--print`，`auto_summary` 用 `--dry-run`。

单次测试排查（`<ts>` 为该天某测试时间戳，如 `20261014_161955`）：

```bash
python3 $T/test_report.py --root $R <ts> --print    # 结果/阶段时刻/收敛/人工测量
python3 $T/parse_controller_log.py --root $R <ts>   # 事件时间线与 STOP 触发
python3 $T/recalc_initial.py --root $R <ts>         # bag 感知复算初始偏差
```

---

## 4. 工具详解

> 以下示例统一以 `data_1013` 为例（时间戳与之一致）；替换为实际日期即可。
> 报告类工具默认输出到 `<root>/reports/`（见 2.1），`--print`/`--out -` 只打印到终端。

### 4.1 list_tests.py — 数据清单

列出全部测试及数据完整性：bag 大小/时长、三个 log 是否存在、生效参数快照、note、人工测量
（无测量显示 `--`，仅有异常说明显示说明文字）。

```bash
python3 tools/list_tests.py --root data_1013
python3 tools/list_tests.py --root data_1013 --date 20261013   # 只列某天
python3 tools/list_tests.py --root data_1013 --detail 142035   # 打印单个测试的全部路径
```

输出列：`测试 | bag | mb | 时长(s) | motion | arc | rec | config快照 | note | 人工测量`。

### 4.2 bag_info.py — bag 话题信息

直读 sqlite3（不依赖 rosbag2），输出每条消息话题、类型、消息数与整体时长。

```bash
python3 tools/bag_info.py --root data_1013 142035     # 某测试全部话题
python3 tools/bag_info.py --root data_1013 --all      # 全部测试的一句话摘要
python3 tools/bag_info.py /path/to/autorecord_xxx     # 也可直接给 bag 目录（无需 --root）
```

### 4.3 extract_topics.py — 导出 CSV 缓存

把常用数值话题导出为 `csv/<ts>/<话题路径>.csv`（列：`time,字段...`，time 为 epoch 秒）。
后续 `load_series()` 会优先读缓存，重复分析时明显加速（bag 约 100MB/次）。

```bash
python3 tools/extract_topics.py --root data_1013 142035          # 默认话题集
python3 tools/extract_topics.py --root data_1013 --all --date 20261013
python3 tools/extract_topics.py --root data_1013 142035 --list   # 查看可导出话题及类型
python3 tools/extract_topics.py --root data_1013 142035 \
        --topics /arc_detector/dist_x_l,/chcnav/devpvt --force     # 指定/强制重导
```

默认话题集：target_point、ground_plane、arch_normal、top_line_dir、phase_state、height、
height_l/r、dist_x_l/r、distance_diff、ground_height_l/r_2d、rect_center_y、chcnav/devpvt、
temperature。支持的消息类型见 `common.DECODERS`（Float64/Point/Vector3/Quaternion/Bool/Int8/
Temperature/Hcinspvatzcb）。

### 4.4 parse_controller_log.py — 控制器 log 解析

两类用途：打印事件时间线；把周期行导出为 CSV 供自行分析。

```bash
python3 tools/parse_controller_log.py --root data_1013 142035
python3 tools/parse_controller_log.py --root data_1013 142035 --events-all --warn
python3 tools/parse_controller_log.py --root data_1013 142035 --cycles-csv   # → csv/<ts>/controller_cycles.csv
python3 tools/parse_controller_log.py --root data_1013 142035 --stage 2 --tail 10 \
        --field dist_x_l,dist_x_r,height_fil,roll_nav,roll_ltg
```

- 事件行默认过滤 `Solve / waiting msg / STARTUP s2 active / roll_ekf` 等噪声；`--events-all`
  查看全部，`--warn` 附带 `[W]` 行，`[E]` 始终显示。
- `--cycles-csv -` 输出到 stdout；CSV 固定 38 列（`FIELDS`），含阶段二全部字段。

### 4.5 test_summary.py — 每日汇总（自动指标 + 人工测量 + 达成率）

两部分输出：

1. **自动指标表**（来自控制器 log）：用时、结果（成功/未停车/未启动/无 log）、初始
   hdg 与 d_head、停车触发、阶段二末 dist_x / height(target, fil) / nav roll / ltg、
   IMU 与油温、note 备注；
2. **人工测量表 + 统计小结**（来自 stats 文件）：五项人工测量、人工 roll、分项达成率、
   全部指标达成率、（`--detail`）未达标明细。

```bash
python3 tools/test_summary.py --root data_1013
python3 tools/test_summary.py --root data_1013 --date 20261013    # 默认写 reports/test_summary.md
python3 tools/test_summary.py --root data_1013 --print            # 只打印
python3 tools/test_summary.py --root data_1013 --from 150000 --to 155715 --exclude 154155
python3 tools/test_summary.py --root data_1013 --manual-only --detail   # 只看人工测量
python3 tools/test_summary.py --root data_1013 --flip-lateral-error \
        --thresholds lat=10,height=5,dist_min=120,dist_max=180,time_max=120
python3 tools/test_summary.py --root data_1013 --no-bag-fallback       # log 缺失时不读 bag
```

筛选参数：`--date` 整日；`--from/--to` 时间戳（`HHMMSS` 或 `YYYYMMDD_HHMMSS`，按时间开始比）；
`--exclude` 逗号分隔的时间戳子串（如 `190233,194313`、`1541`）。

### 4.6 auto_summary.py — 对位结果统计表（旧版格式）

输出与旧 `scripts/auto_summary.py` 相同格式的 `## 对位结果统计` 段：单表
（人工测量 6 列 + 初始航向/横向偏差）+ 统计小结 + 未列入列表；初始偏差取自 log 首条
`STARTUP strategy active`，缺失时从 bag 感知复算。

```bash
python3 tools/auto_summary.py --root data_1013 --dry-run --detail   # 打印；不带 --dry-run 写 reports/auto_summary.md
python3 tools/auto_summary.py --root data_1013 --from 142035 --to 155715 --exclude 154155
python3 tools/auto_summary.py --root data_1013 --note "roll_cmd_smooth 对比"   # 写 reports/auto_summary.md
```

- 输出默认写 `<root>/reports/auto_summary.md`（`--out` 可改路径；文件已存在则**替换其中 `## 对位结果统计` 段**，其余内容保留），
  适合维护固定的统计文档；
- `--flip-lateral-error`：车头偏左取反（对齐其他偏差信号符号）；
- `--thresholds / --target / --detail / --dry-run / --no-bag-fallback` 同 test_summary。

### 4.7 test_report.py — 单测试报告

单次测试的完整报告：数据元信息、人工测量、结果与停车触发、阶段时刻（相对 `init -> 1a`）、
阶段二末次周期全部字段与收敛判断、停车窗口（PHASE2-STOP）均值、温度。

```bash
python3 tools/test_report.py --root data_1013 150522
python3 tools/test_report.py --root data_1013 150522 --events-all   # 附全部事件行
python3 tools/test_report.py --root data_1013 150522 --print   # 默认写 reports/test_report_150522.md
```

### 4.8 params_table.py — 参数与温度记录表

每个测试一行：生效快照、IMU/油温（log 后 30% 均值）、roll_target 与 height target（log 众数）、
height 温度补偿（按 config 分段查表）、人工高度差与补偿、阶段二末 nav/ltg/hlr/height、
lift/roll 收敛、chcnav 停稳均值；文末附全部快照的参数变化（相对上一快照）。

```bash
python3 tools/params_table.py --root data_1013   # → reports/params_table.md
python3 tools/params_table.py --root data_1013 --from 142035 --to 155715 --no-chcnav
```

### 4.9 phase2_stats.py — 阶段二 ground height / ltg 统计

区间定义：阶段二开始（log `STAGE 1d -> 2`）→ `PHASE2-STOP active`（不含）。
输出 roll_target、chcnav 窗口均值、log ltg 窗口均值、ltg 区间估算、ltg 停稳估算
（STOP 后末尾 50 点）、height target、gh_l/r 与 height_l/r 均值、末 hlr/nav/dist_x、
人工高度差与人工 roll。

```bash
python3 tools/phase2_stats.py --root data_1013   # → reports/phase2_stats.md
python3 tools/phase2_stats.py --root data_1013 --from 142035 --all   # 失败测试也列出
```

> **ltg 读数注意**：阶段二刚开始时 ltg（= gh−height 左右差）受过渡段影响可达数度，
> 区间均值不代表车辆水平状态；评估停稳后的真实水平请用 **ltg 停稳估算** 列。

### 4.10 recalc_initial.py — 初始偏差复算

从 bag 感知数据按控制器同公式复算 `STARTUP` 的 y_R/hdg/d_head/d_tail（log 无 STARTUP 行
或需要核对感知原始数据时使用）。

```bash
python3 tools/recalc_initial.py --root data_1013 142035
python3 tools/recalc_initial.py --root data_1013 183808 --src 0 --tail 8.5
python3 tools/recalc_initial.py --root data_1013 183808 --time 3   # 只取前 3s 样本
```

### 4.11 config_diff.py — 参数快照对比

按时间顺序对比 `configs/` 下的参数快照（默认文件
`configs/newdf_dpt15/control/control_config.yaml`），输出 yaml 格式变更块；`--assoc`
附“测试 → 生效快照”映射。

```bash
python3 tools/config_diff.py --root data_1013
python3 tools/config_diff.py --root data_1013 --from 20261013_150000 --nest
python3 tools/config_diff.py --root data_1013 --baseline /path/to/control_config.yaml   # → reports/config_changes.md
```

### 4.12 gen_roll_temp_comp.py — roll 分段温度补偿参数生成（多天）

汇聚一个或多个数据根目录的 `(温度, roll_target, |lift 左−右|)` 样本，按温度分段（默认 5°C），
每段取 |高度差| 均值最小的 roll_target 为最优；补偿 = 最优 − 基准；相邻段最优值相同则合并。
用于生成 `roll_temp_comp_segments / roll_temp_comp_values`（兼容旧
`scripts/startup_strategy/gen_roll_temp_comp.py` 的算法）。

```bash
python3 tools/gen_roll_temp_comp.py --root data_1015 --print
python3 tools/gen_roll_temp_comp.py --root data_1013 --root data_1015 --temp-source oil
python3 tools/gen_roll_temp_comp.py --root data_1012 --root data_1014 --base -0.18 --bin 5 --min-n 3
```

- `--root` 可重复指定多个（缺省自动探测单个）；样本不足或 roll_target 只有一种取值时报告
  会给出警告（无法在段内寻优，需要含多种 roll_target 的试验数据）；
- `--temp-source imu|oil|env`：温度键取 IMU（log `temp=`，默认）/液压油温（log `oil_temp=`）/
  环境温度（`/temperature_env`）——从稳定性分析看油温最一致，环境温度不建议；
- `--base` 默认取样本 roll_target 众数；`--bin/--min-n` 同旧工具（5°C / 3）。

### 4.13 gen_stats_template.py — 人工测量模板生成

扫描 `bags/`、`logs/` 中的测试时间戳，按天生成 `stats/stat-<YYYYMMDD>.md` 模板：每个测试
一节，五项字段只写字段名、数值留空由人工填写（未填数值的测试自动视为“未测量”）。
默认**增量**追加缺失小节（已有内容和填写值原样保留）；`--force` 整份重建并备份 `.bak`。

```bash
python3 tools/gen_stats_template.py --root data-1016              # 写入 stats/stat-20261016.md
python3 tools/gen_stats_template.py --root data-1016 --print      # 只看不写
python3 tools/gen_stats_template.py --root data-1016 --from 150000 --to 152000
python3 tools/gen_stats_template.py --root data-1016 --time -     # 时间字段也留空
```

### 4.14 run_all.sh — 一键批量运行

对指定数据根目录依次执行：`list_tests`（→ `reports/tests.txt`）、`extract_topics --all`
（建缓存）、`test_summary`、`auto_summary`、`params_table`、`phase2_stats --all`、
`config_diff --assoc`、`gen_roll_temp_comp`（→ `reports/roll_temp_comp.md`）、
`bag_info --all`（→ `reports/bag_info.txt`）、逐测试 `test_report`
（→ `reports/test_report_<ts>.md`）。任一步失败会记录并在结尾汇总（退出码 1），不影响其余步骤。

```bash
bash tools/run_all.sh --root data_1014
bash tools/run_all.sh --root data_1014 --no-extract --no-reports   # 快速模式（不上 bag）
bash tools/run_all.sh --root data_1014 --date 20261014 \
     --from 150000 --to 160000 --exclude 154155
bash tools/run_all.sh --root data_1014 --refresh-csv               # 强制重建 CSV 缓存
bash tools/run_all.sh --root data_1015 --roll-roots data_1013,data_1014  # roll 温度补偿多天合并
```

| 参数 | 说明 |
|---|---|
| `--root DIR` | 数据根目录（缺省自动探测，同其它工具） |
| `--date/--from/--to/--exclude` | 过滤测试子集（`--exclude` 仅作用于 test_summary/auto_summary） |
| `--no-extract` | 跳过 `extract_topics --all`（不读 bag，最快） |
| `--roll-roots d1,d2` | `gen_roll_temp_comp` 额外合并的数据根目录（缺省仅当前 root；单天通常 roll_target 单一，会给出警告） |
| `--no-roll-comp` | 跳过 `gen_roll_temp_comp` |
| `--no-reports` | 跳过逐测试 `test_report` |
| `--refresh-csv` | 已存在的 CSV 缓存也重新导出 |

### 4.15 fetch_remote_data.sh — 从车端同步数据（附带的原有脚本）

```bash
bash tools/fetch_remote_data.sh -l -d data_1013   # 只同步 logs → data_1013/logs/
bash tools/fetch_remote_data.sh -b -d data_1013   # 只同步 bags → data_1013/bags/
bash tools/fetch_remote_data.sh -c -d data_1013   # 只同步 configs → data_1013/configs/
bash tools/fetch_remote_data.sh -n                # 预览（dry-run）
```

默认同步到 `<仓库>/data_<今天>/`（`logs/`、`bags/`、`configs/` 分列其下）；环境变量 `REMOTE` 可覆盖
远程主机（默认 `b1-aibox`）。

---

## 5. 指标口径

### 5.1 自动指标（控制器 log）

| 指标 | 口径 |
|---|---|
| 用时 | `STAGE init -> 1a` → `CONVERGED -> publish /control/stop` 墙钟差 |
| 结果 | 成功（有 CONVERGED）/ 未停车 / 未启动（无 STAGE）/ 无 log |
| 初始偏差 | 首条 `STARTUP strategy active` 的 `hdg` / `d_head` / `y_R`；缺失时 bag 感知复算（前 5 样本均值） |
| 停车触发 | `PHASE2-STOP active: min_dist_x=... <= ...` 或 `PHASE1-STOP active: x_R=...` |
| 阶段二末值 | 末次 `[2]` 周期行：`dist_x`、`height=(target, hfil)`、`roll=(nav, ltg, hlr)` |
| 收敛 | 末次 `[2]` 的 `|lift fin| ≤ 5%`、`|roll fin| ≤ 5` |
| 温度 | 周期行后 30% 均值（IMU `temp`；油温 `oil_temp`） |
| 停车窗口 | `PHASE2-STOP active` → `done`（车静止段）内信号均值 |

### 5.2 人工测量与达成率（stats 文件）

- **车头偏左**：mm，`--flip-lateral-error` 可反转符号；
- **高度差-左/右**：lift X Y，mm；人工 roll = `atan2((X−Y)/1000, 1.6 m)`；
- **停车距离-左/右**：车头左右距离 X Y，mm；
- **时间**：秒；
- **达成率**：默认阈值 `lat=10`、`height=5`、`dist∈[120,180]`、`time≤120`（`--thresholds` 可改）；
  全部指标达成率 = 四项同时满足。

### 5.3 感知信号与滤波（看数时注意）

控制器对多数感知信号做了滤波/剔尖后才用于控制，**log 周期行的值与 bag 原始值可能不同**：
y_R/z_R、roll（nav/topline/ltg 多来源 + 温度补偿）、distance_diff、rect_center_y、
dist_x_l/r、heading 补偿等。排查感知问题时：原始值看 bag（`/arc_detector/*`），
控制用值看 log 周期行（本工具解析的字段即为控制用值）。

---

## 6. 典型工作流

### 6.1 每日试验汇总

```bash
python3 tools/list_tests.py --root data_1013            # 先确认数据齐全
python3 tools/test_summary.py --root data_1013 --detail
python3 tools/auto_summary.py --root data_1013
```

关注：达成率变化、超限项、note 中记录的异常（note 备注列自动带上）。

### 6.2 定位单次失败

```bash
python3 tools/test_report.py --root data_1013 <ts> --print   # 先看结果与阶段时刻
python3 tools/parse_controller_log.py --root data_1013 <ts> # 看事件与 STOP 触发
python3 tools/parse_controller_log.py --root data_1013 <ts> --stage 2 --tail 20 \
        --field dist_x_l,dist_x_r,dd_fil,height_fil,roll_nav,roll_ltg
```

再结合 bag 原始话题与 `data_<日期>/notes/` 中的备注判断是感知跳变、积水、y_R=nan 还是
控制逻辑问题。

### 6.3 温度/参数与 roll 分析

```bash
python3 tools/params_table.py --root data_1013   # → reports/params_table.md
python3 tools/phase2_stats.py --root data_1013   # → reports/phase2_stats.md
python3 tools/config_diff.py --root data_1013 --assoc
```

`params_table` 的 `target+高度差补偿` 与 `chcnav+高度差补偿` 可直接评估 roll 目标设定；
`phase2_stats` 的 `ltg 停稳估算` 与人工 roll 对照可评估 ltg 信号质量。

### 6.4 配置变更 → 试验效果追溯

1. `config_diff.py --assoc` 找到每次试验生效的快照及变更点；
2. `list_tests.py` / `test_summary.py` 按时间窗口（`--from/--to`）圈定变更后的试验；
3. 用 `--exclude` 剔除 note 标记异常的测试后重新统计达成率。

---

## 7. 自写分析

### 7.1 共享库 API

```python
import sys; sys.path.insert(0, "tools")
import common as C, metrics as M

C.init("data_1013")                       # 或省略（自动探测/环境变量）
t = C.resolve_test("142035")              # 测试对象（bag/log/config/note/stats）
cycles = C.parse_cycles(t.motion_log)     # 周期行 dict（阶段一/二全部字段）
events = C.log_events(t.motion_log)       # STAGE/STARTUP/STOP/CONVERGED 事件
series = C.load_series(t, "/arc_detector/dist_x_l")   # [(epoch, value)]，优先 csv 缓存
m = C.manual_stats(t)                     # 人工测量 {head, lift_l, lift_r, dist_l, dist_r, time, marker}
d = M.summarize(t)                        # 汇总指标（test_summary 同源）
```

常用工具函数：

- `C.discover_tests()`：全部测试；`C.bag_topics(bag)` / `C.bag_series(bag, topic)`；
- `C.mean_window(series, t0, t1)`、`C.interp_series(series)`、`C.wall_to_epoch(test, wall)`；
- `C.initial_deviations(test, src=1)`：初始偏差逐样本复算；
- `C.align_table(header, rows)`：CJK 等宽 markdown 表；
- `C.init(root)` 之后 `C.BAGS / C.LOGS / C.CONFIGS / C.NOTES / C.STATS / C.CSV` 均为当前根下路径。

### 7.2 直接读 bag（无缓存时）

```python
from common import bag_series, cd_f64
for ts, data in bag_series("<root>/bags/autorecord_xxx", "/arc_detector/height"):
    print(ts, cd_f64(data))
```

bag 就是 sqlite3：表 `topics(id,name,type)` / `messages(topic_id,timestamp,data)`（CDR，
前 4 字节头），字段解码偏移见 `common.py` 的 `cd_*` 函数。

---

## 8. 与旧脚本对照

| 旧脚本（scripts/） | 新工具 | 说明 |
|---|---|---|
| `auto_summary.py` | `auto_summary.py` / `test_summary.py` | 人工测量来源从 README 改为 `stats/`；初始偏差回退从 csv 改为 bag |
| `gen_params_table.py` | `params_table.py` | 参数快照/温度/收敛同口径，去除了 README 依赖 |
| `ground_height_phase2.py` | `phase2_stats.py` | 直接读 bag/缓存，新增 log ltg 窗口列 |
| `recalc_startup_initial.py` | `recalc_initial.py` | 直接读 bag（无需 csv） |
| `diff_round_config.py` | `config_diff.py` | 按天快照链自动对比 |
| `startup_strategy/gen_roll_temp_comp.py` | `gen_roll_temp_comp.py` | 样本从 params_table.md 改为直接读多天数据（stats+log） |
| `new_round.sh` | — | 新存储自动按天备份，无需新建轮次 |
| `update_readme_temperatures.py` | 并入 `test_summary.py` / `params_table.py` | 温度列自动输出 |
| `bag_info.py` | `bag_info.py` | 改为 sqlite3 直读 |
| `renumber_report_table.py` | — | 暂无对应（report.md 不在新结构中） |

---

## 9. 注意事项与 FAQ

- **工具位置**：`tools` 可放任意位置；`--root` 或 `DATA_ROOT` 指向数据即可。默认探测
  当前目录、工具目录及其上级，若都不匹配则取同级最新的 `data_*` / `data-*` 目录（命中
  多个时 stderr 打印 `[info] 自动选择最新数据目录…`，请核对输出标题中的日期或显式
  `--root`）。普通 `data/` 目录不在自动选择范围内，需显式 `--root data`。
- **无数据根用法**：`bag_info.py /path/to/bag_dir` 可直接分析任意 bag 目录，不需要数据根。
- **报告输出**：报告类工具默认写 `<root>/reports/`，终端只看加 `--print`（或 `--out -`）。
  报告属于派生结果，如需忽略可自行把 `data_*/reports/` 加入 `.gitignore`。
- **CSV 缓存**：`<root>/csv/` 为派生数据，建议加入 `.gitignore`（仓库已配置 `data_*/csv/` 等）。
- **时间基准**：bag 时间戳为 epoch；log 只有墙钟，跨零点测试（如 23:5x 开始）由
  `wall_to_epoch` 依据 bag 起始日期处理；一天一个根目录时无歧义。
- **字段兼容**：解析器兼容旧版周期行（无 `ltg`、`distance_diff=`、无 `dist_x`）与
  新字段（`dd= / gh= / dist_travel= / source=`）；`phase_state` Bool/Int8 均可解码。
- **过滤值 vs 原始值**：周期行是控制用值（已滤波）；原始值请读 bag 或 csv 缓存。
- **`有问题` 小节**：不会进入统计；`test_summary.py` 会把它列在“未列入（未测量/异常）”。
- **性能**：不建缓存时 `phase2_stats.py --all` 约几十秒（需读 bag 数值话题）；
  建缓存后明显加快。其余工具为秒级。
- **缺失回退**：log 无 STARTUP 行时，`test_summary`/`auto_summary` 默认用 bag 感知复算初始
  偏差（`--no-bag-fallback` 关闭）；无 bag 话题的测试标 `-`。
