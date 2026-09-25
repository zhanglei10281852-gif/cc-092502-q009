# 考古研究协作基础服务

这是一个供考古项目扩展业务模块的纯后端基础服务，提供研究项目登记、成员与角色、会话认证、审计事件、幂等请求和可恢复后台任务。服务使用 FastAPI 与 SQLite，不依赖另行部署的数据库、缓存或队列。

## 区域年代序列模块

在基础服务之上实现了面向"鲍家遗址及相邻遗址文化阶段衔接"研究的年代序列证据后端（`app/chrono.py`、`app/chrono_service.py`、`app/chrono_api.py`）。

### 信息层级（强制分离）

| 类别 | 证据形态 | 参与求解 |
| --- | --- | --- |
| `observation` 观测证据 | 测年概率区间、类型学出现范围、出土遗物 | 是 |
| `inference` 推断关系 | 带出处的层位先后（可附间隔年数） | 是 |
| `label` 暂定标签 | 文化阶段标签（如"崧泽文化早期（暂定）"） | 否，仅标注 |

### 年代与求解语义

* 年份为有符号整数日历年，公元前为负；开放（未知）边界一律为 `null`，系统**绝不虚构点估计**。
* 每个文化层是时间区间 `[x, y]`（`x<=y`）。测年区间与类型学范围按"可相交"解释：证据只断言文化层形成时间与该区间存在交点，概率值只登记、不参与收窄。
* 层位先后为严格早于：`y_早 + max(gap,1) <= x_晚`。
* 求解为差分约束传播（Bellman-Ford 可行性判定 + 相对零点最紧界），结果与输入顺序无关；计算结果按证据集内容哈希缓存，保证可复现。
* 材料不相容时返回**包含极小冲突核**（核内删去任一证据即恢复相容），可枚举多个核。

### 证据快照与计算

`POST /api/projects/{pid}/snapshots` 固化一组证据（含内容哈希），`POST .../snapshots/{id}/compute` 计算满足约束的时间范围；`GET .../chronology` 支持 `from_year`/`to_year`/`region_id` 过滤——未知边界保守保留，不因未知而剔除遗址。

### 文化阶段方案工作流

`proposed → in_review → published`（`rejected` 由反对票阻断）。评审要求：提案人不能自评、每位评审人一票、达到 `required_reviews` 且无反对票方可由负责人发布。发布内容固化到 `scheme_versions`（不可覆盖），结论行含阶段边界与支撑证据。新证据只能创建**候选修订**（`POST .../schemes/{id}/revisions`），自动生成受影响遗址、边界与结论的差异摘要（`candidate_diff`）。

### 引用撤回联动

`POST .../citations/{id}/retract` 后，凡支撑证据引用该出处的已发布结论、以及快照中含相关证据的在途候选，自动追加 `review_flags`（待复核）；发布版本本身不被修改。

### 离线导入导出

```bash
python -m app.cli export --project-id 2 -o bundle.json
python -m app.cli import -i bundle.json --new-code REG-COPY
```

导入为整包事务，id 全部重映射，导入后逐快照复算并校验结果哈希一致（`verified`）。评审投票记录引用源库用户，目标库无对应用户时跳过并计数（`skipped_reviews`）。

### 权限

写操作（名录、证据、快照、提议、撤回）要求 `owner/researcher/recorder`；评审要求 `reviewer`（或 `owner`）；发布仅 `owner`；读取要求项目成员（含 `viewer`）。全部变更写入审计事件。

## 环境与安装

运行环境为 Python 3.11。安装开发依赖：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## 初始化与启动

```bash
python -m app.cli init-db
uvicorn app.main:app --host 0.0.0.0 --port 8432
```

基础接口包括 `/api/system/health`、`/api/projects`、`/api/users`、`/api/sessions`、`/api/audit` 和 `/api/jobs`。首次启动后可用命令行创建管理员，也可以通过测试夹具构造隔离数据库。

## 测试

```bash
python -m pytest
```

测试覆盖数据库初始化、项目成员权限、会话撤销、审计脱敏、幂等写入和后台任务领取与完成；年代序列模块以固定数据集（`tests/fixtures/baojia_dataset.py`）覆盖区间传播、开放边界、冲突诊断、并发评审、发布不可变、修订差异、撤回联动、导入导出与结果可复现性。

## 编译检查

```bash
python -m compileall -q app tests
```

## API 冒烟

```bash
python -m app.cli smoke
```

该命令在进程内检查根路径、健康接口、数据库外键和 WAL 配置。

## 扩展约定

新研究模块应通过独立路由、服务和仓储接入，跨表写入放在即时事务中。外部标识、幂等键和审计载荷应保存原始值及规范化值；后台任务使用 SQLite 租约，不允许依赖外部队列。用户口令和会话令牌只保存摘要，审计事件会过滤密码、令牌等敏感字段。
