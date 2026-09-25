# 考古研究协作基础服务

这是一个供考古项目扩展业务模块的纯后端基础服务，提供研究项目登记、成员与角色、会话认证、审计事件、幂等请求和可恢复后台任务。服务使用 FastAPI 与 SQLite，不依赖另行部署的数据库、缓存或队列。

**区域年代序列证据模块**（v1.1 新增）面向"鲍家遗址如何衔接周边遗址"这类问题，把混在一张表里的材料拆分为三类并分别管理：

| 类别 | 内容 | 是否参与数值传播 |
| --- | --- | --- |
| `observation` 观测证据 | 测年概率区间（AMS/OSL，可多概率段）、器物类型学出现范围 | 是（硬边界） |
| `inference` 推断关系 | 带出处的文化层先后约束（可含间隔年数） | 是 |
| `tentative_label` 暂定标签 | 研究者对层位所贴的文化阶段标签 | 否，仅展示 |

核心原则：**只表达由证据必然推出的范围**。开放边界与缺失年代序列化为 `null`（未知），系统在任何情况下都不生成点估计；概率值只用于展示，不改变硬约束。

## 环境与安装

运行环境为 Python 3.11。安装开发依赖：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt   # 或 pip install -e ".[dev]"
```

## 初始化与启动

```bash
python -m app.cli init-db
uvicorn app.main:app --host 0.0.0.0 --port 8432
```

基础接口包括 `/api/system/health`、`/api/projects`、`/api/users`、`/api/sessions`、`/api/audit` 和 `/api/jobs`。

年代序列接口挂在 `/api/projects/{project_id}/chron/` 下：

- `sites` / `layers` / `sources`：遗址、文化层、出处登记（出处可撤回）
- `evidence/dating` / `evidence/typology` / `evidence/ordering` / `evidence/label`：四类证据登记；`PUT` 同类路径做**只追加修订**（旧版本置 `superseded`，不就地覆盖）
- `snapshots`：证据快照，固定证据 id+版本+内容哈希；`POST snapshots/{key}/compute` 求解
- `schemes`：文化阶段方案，工作流为 `proposed →（同行评审）→ published`；已发布版本永久不可覆盖，新增证据只能经 `revisions` 产生候选版本并附差异摘要
- `flags`：引用撤回后自动挂起的"待复核"结论
- `timeline`：按时间（cal BP）与区域过滤的当前发布年表 JSON

### 区间传播与冲突诊断

求解器（`app/chronology/solver.py`）是无 I/O 的纯函数：每条证据转为 cal BP 整数轴上的差分约束，边界传播到不动点。结果带 `algorithm` 版本号与 `result_hash`；同一快照任意次重算哈希必须一致，否则返回 `result_not_reproducible`。

证据不相容时不给出年表，而是返回倒置变量与**包含最小冲突集合**（删除法求得；删去其中任一证据即恢复相容），并附完整证据推导链。

### 文化阶段方案治理

- 提议需要 `owner`/`researcher` 角色；评审需要 `reviewer`/`owner`，且**提议人不能批准自己的方案**；
- 每个候选版本要求可配置数量的独立批准（默认 2），数据库唯一约束保证同一评审人恰好一票（含真实并发）；
- 评审可 `approve` / `request_changes` / `comment`；要求修改后可在候选版本上继续迭代，已发布版本始终保留；候选版本可撤回；
- 候选修订自动生成差异摘要：新增/移除证据、阶段归属变化、各层位边界变化、受影响遗址与结论清单；
- 发布前若存在待复核标记则拒绝发布。

### 引用撤回联动

出处撤回（`sources/{key}/retract`）不删除任何数据：所有现存候选与已发布版本中、层位依赖该出处的阶段结论自动写入 `pending` 待复核标记；已撤回出处不能再支撑新证据。研究者核对后通过 `flags/{id}/resolve` 关闭标记。

### 离线导入导出

```bash
python -m app.cli chron-seed-demo                 # 写入鲍家遗址固定演示数据集
python -m app.cli chron-export --project-id 1 --out bundle.json
python -m app.cli chron-import --file bundle.json --project-code BAOJIA-COPY
```

导出包（`regional-chronology-export/v1`）含遗址、层位、出处、全部证据版本、快照固定规格、缓存计算结果与方案版本/评审；导入到非空项目会被拒绝。快照内固定的证据 id 会重映射，而内容哈希不变——因此导入后对同一快照重算的 `result_hash` 与原库逐位一致（跨库可复现）。

HTTP 接口同样提供 `GET /api/projects/{id}/chron/export` 与 `POST /api/projects/chron/import`。

### 权限角色

`owner`（管理/发布）、`researcher`（登记/提议）、`recorder`（登记证据）、`reviewer`（评审）、`viewer`（只读）。所有写操作在 `IMMEDIATE` 事务内完成并写脱敏审计事件。

## 测试

```bash
python -m pytest
```

31 个测试覆盖：数据库与基础协作（原基线）、纯求解器的区间传播/开放边界/最小冲突诊断/哈希可复现、固定数据集端到端求解、快照钉扎与证据修订、提议-评审-发布-撤回-再修订全流程（含 8 线程真实并发评审）、引用撤回待复核联动、时间与区域过滤、权限矩阵，以及 CLI 播种-导出-导入的跨库结果复现。

## 编译检查

```bash
python -m compileall -q app tests
```

## API 冒烟

```bash
python -m app.cli smoke
```

## 扩展约定

新研究模块应通过独立路由、服务和仓储接入，跨表写入放在即时事务中。外部标识、幂等键和审计载荷应保存原始值及规范化值；后台任务使用 SQLite 租约，不允许依赖外部队列。用户口令和会话令牌只保存摘要，审计事件会过滤密码、令牌等敏感字段。
