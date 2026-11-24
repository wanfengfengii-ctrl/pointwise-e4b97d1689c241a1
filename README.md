# 材料试验站连续测振归档 V1

夜班值班员从单台测振仪开启录制并登记关注片段。归档停写期间，边缘先耐久接管原帧再放行仪器。归档恢复后补送，归档员逐窗签发，质检员停源后仍可按成果号领取原证据和峰峰值。

## 一班的工作

1. 开启录制，把录制号绑定到设备给出的递增原号、整数读值和原出处。
2. 登记 A、B 两个闭区间窗口，确认各自的应领原号。
3. 从设备头读取下一帧，在边缘事务中固定该帧及当时覆盖它的全部窗口，同时推进登记截止前沿。
4. 接管提交后释放源帧。释放回包丢失时，重开按原帧号确认已生效的释放，再采下一帧。
5. 归档可写时逐项补送。每个已提交接纳只结清对应窗口与原号。
6. 按窗口查齐备情况。尾帧已经接纳时，仍逐一检查所有应领原号。
7. 计算本窗最大读值减最小读值，提交固定正文和成功回执。
8. 停止录制，重开归档后按成果号领取或导出已保存的证据。

## 运行

需要 Python 3.11 标准库。在项目根目录运行平台提供的完整回归入口。

```sh
python3 -B check_project.py
```

演示使用真实的两库交接与独立手算对照。默认在临时目录创建运行数据。

```sh
python3 -B demo.py
```

如需在项目内保留演示状态，可运行 `python3 -B demo.py --state-dir .demo-state`。重复初始化补齐数据库结构并保留已有数据。相同录制及登记复用原进度，已成果窗复用原成果，其他录制及合法追加登记独立保留。相同标识对应不同录制或窗口参数时报冲突。

## 公开 Python 接口

`station.py` 提供以下入口。边缘连接由调用者关闭，`ArchiveStore` 可单独作为上下文管理器使用。数据库路径分别指向不同的 SQLite 文件。并行调用者各用自己的连接。同一连接上的公开写操作管理自身提交边界。

| 入口 | 输入与结果 |
| --- | --- |
| `initialize_edge(path)` | 打开边缘连接并初始化仪器模拟端、登记、接管及义务结构。 |
| `ArchiveStore(path)` | 打开并初始化归档库。`initialize_archive(store)` 可重复执行。 |
| `open_recording(edge, recording_id, frames)` | 固定录制定义，返回 `Recording`。重投原定义保留设备头和现有进度。 |
| `register(edge, recording_id, window_id, left, right)` | 返回固定 `WindowRegistration`，其中 `interval.expected` 是窗口应领原号元组。 |
| `advance(edge, recording_id, issue_release=...)` | 补认先前未确认的释放，再耐久接管并放行下一帧。`AdvanceResult.at_end` 表示录制尾。默认使用确定性设备端口，可用 `ReleasePort` 指定一次未生效或丢回包的中断。 |
| `deliver(edge, archive, recording_id, limit=None, intake=...)` | 补送边缘待交付项。报告逐项 `accepted`、剩余数 `remaining` 和归档写入或链路中断信息 `blocked`。省略限制时尝试全部待项。 |
| `finalize(archive, recording_id, window_id)` | 签发齐备窗口或归还原来已签的 `PublishedResult`。 |
| `get_result(archive, result_id)` | 按正整数成果号读取固定正文及回执。 |
| `export_result(archive, result_id)` | 返回可核对的 JSON 文本，包含成果号、冻结明细、`device_count` 计量及回执。 |

`Frame(number, value, origin)` 使用整数原号、整数读值和非空 UTF-8 原出处。布尔值不作为整数输入。原号按数字严格递增，允许跳号和空录制。闭区间端点须在录制的原号范围内，且至少有一个实测原号。新窗口以首个应领原号的耐久接管为截止，同号同参可在截止后取得原登记。标识在录制内区分窗口，在同库内区分录制。

`ContractError` 表示无效请求，`Conflict` 表示身份或定义冲突，`LateRegistration` 表示新登记已截止。未知录制、尚无归档窗口定义或未知成果报 `NotFound`。已知但未齐备的窗口报 `IncompleteWindow`，其 `missing` 元组包含缺号。计量只使用齐备的原始整数读值，零、负值和大整数均有效，单帧峰峰值为零。读回和导出使用固定正文中的数字原号顺序及原精度。

设备释放在接管提交后的独立事务推进，并留下精确到原帧的事实。链路中断报 `LinkUnavailable`，可原样续办。归档接纳提交后才返回确认。拒写、失配或未收到确认的交付可重投。归档的每个窗口独立保存接纳关系，发布正文和成功回执要么同时提交，要么同时回滚。归档接纳可由 `archive.intake.accept_delivery` 调用，传入冻结登记及原帧以按窗接纳。`archive.completeness.inspect_window_progress` 可在归档读视图内查询实测明细和缺号。

## 手算样例与回归

`examples/recording.json` 给出原号 10、11、12，读值为 4、9、6。A 领 10、11，峰峰值为 5。B 领 11、12，峰峰值为 3。两件中 11 的出处都是 `recorder/31/11`，仪器成功释放共三次。`examples/expected.json` 保存逐项对照。演示在帧 11 放行后丢失回包并重开边缘，先签 A，再重开归档签 B，经真实领取及导出核对后输出场景结果。

回归另覆盖登记重投与截止、接管失败时保留读头、归档拒写时采到录制尾、补送部分提交后重开、尾帧先到、非法或冲突证据、零及大整数计量、数字原号导出顺序，以及真实提交失败后 B 先签和 A 原号续办。
