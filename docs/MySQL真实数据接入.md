# MySQL 真实数据接入

已实现后端接口：已有 MySQL → 只读一致性快照 → 独立 CSV 目录。可在 FastAPI 的 `/docs` 页面操作，暂未增加前端连接管理页。

现有 Olist 看板继续使用原数仓。本接口不执行覆盖式 ETL、不修改源库、不把新表加入智能问数白名单。

## 1. 在服务器配置连接

复制 `D:\DataMind\backend\sources.example.json` 为同目录下的 `sources.local.json`，填写业务库的 host、port、database、username、允许读取的 tables、currency、timezone。

- `shop` 是数据源 ID，只允许小写字母、数字、下划线，以字母开头。
- `tables` 必须逐表列出，接口只展示并读取这些表。只支持 InnoDB 基表，不支持视图或任意 SQL。
- `currency` 例如 CNY、BRL；`timezone` 例如 Asia/Shanghai。这两个值记录在任务清单中，不会自动换汇或转换时间。
- 远程数据库可增加 `"tls_ca": "D:/certs/mysql-ca.pem"`，使用 CA 和主机名校验。
- 使用源库专用 SELECT 账号，仅授权列出的业务表。接口开启只读事务，但连接测试不代表已经审计账号的全部权限。无需使用 root。
- 源库需要支持 `MAX_EXECUTION_TIME` 的 MySQL 5.7.8+ / 8.x；当前实现未适配 MariaDB。

在 `D:\DataMind\agent\.env` 末尾添加（不要替换原有配置）：

```dotenv
DATAMIND_INGEST_TOKEN=在本机生成的至少32位随机令牌
DATAMIND_SOURCE_SHOP_PASSWORD=源库专用只读账号的真实密码
```

密码变量名为 `DATAMIND_SOURCE_数据源ID大写_PASSWORD`。真实密码不放进 JSON、URL 或请求体。配置示例见 `agent/.env.example`。

随机令牌可在本机生成：

```powershell
D:\python3.12.10\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

修改环境变量后重启后端。`sources.local.json` 每次请求读取，无需重启。后端、CSV 快照与凭据文件均应限制为管理员可访问；对外部署请使用 HTTPS 和网络访问控制。

## 2. 连接、查看字段、预览

打开 `http://localhost:8000/docs`，在 **Authorize** 中填入令牌（只填令牌本身）。使用 **MySQL 数据接入** 分组：

| 接口 | 用途 |
| --- | --- |
| `GET /api/sources` | 查看已配置的数据源 ID、币种、时区 |
| `GET /api/sources/contracts` | 查看 8 类实体的输出文件名和必要字段 |
| `POST /api/sources/shop/test` | 测试连接、只读事务和白名单表元数据 |
| `GET /api/sources/shop/tables` | 查看允许表的字段与类型 |
| `POST /api/sources/shop/preview` | 按映射预览最多 20 行 |
| `POST /api/sources/shop/imports` | 执行有界快照，等待完成并返回清单 |
| `GET /api/sources/imports/{job_id}` | 查询落盘的任务状态 |

所有接口均需 `Authorization: Bearer <令牌>`。未配置至少 32 位令牌时返回 503，未授权时返回 401。

预览请求示例（源表与字段必须根据实际库调整）：

```json
{
  "entity": "orders",
  "table": "orders",
  "columns": {
    "order_id": "id",
    "customer_id": "buyer_id",
    "order_status": "status",
    "order_purchase_timestamp": "created_at"
  }
}
```

映射方向为 **中台字段名 → 源表字段名**。相同名称也要显式映射。支持字段选择与改名，不执行 SQL 表达式、JOIN、状态转换或金额换算。请先在源库准备字段含义正确的业务表，或在核对后增加专门的转换步骤。

响应统一为 `{"code":200,"message":"success","data":...}`。预览的值以字符串返回，金额不经过浮点转换；NULL 导出为空 CSV 单元格，与当前 ODS 的读取方式一致。

## 3. 导入快照

请求示例：

```json
{
  "job_id": "0123456789abcdef0123456789abcdef",
  "max_rows_per_table": 100000,
  "mappings": [
    {
      "entity": "orders",
      "table": "orders",
      "columns": {
        "order_id": "id",
        "customer_id": "buyer_id",
        "order_status": "status",
        "order_purchase_timestamp": "created_at"
      }
    }
  ]
}
```

此例只导出订单。可在 mappings 中同时提供 order_items、customers、products、sellers、order_payments、order_reviews、product_category_name_translation。必要字段以 `/contracts` 为准。

`job_id` 是客户端生成的 32 位 UUID hex，可用 Python 的 `uuid.uuid4().hex` 生成；也可省略，由服务生成。建议客户端预先生成并保存：HTTP 连接超时后仍可凭该 ID 查询。已有 ID 返回 409，不重复执行；失败后重试要换新 ID。

成功文件位于：

```text
D:\DataMind\data\ingestion\<job_id>\
  manifest.json
  olist_orders_dataset.csv
  ...本次映射涉及的其他 CSV
```

manifest 包含 source_id、状态、字段映射、币种、时区、时间和逐表行数。只有 `status=succeeded` 的目录可作为快照使用。正常失败删除本次全部 CSV，并保留失败清单；服务被强制终止后，查询状态会返回 interrupted，残留 CSV 不可使用。

单表默认上限 10 万行，可配置到 50 万；整个任务最多 8 个实体、200 MiB，单字段最多 1 MiB。总连接/读取时间上限 120 秒，每条 SELECT 在 MySQL 中上限 15 秒，同一进程最多同时连接 2 个源库会话。预览响应最多 2 MiB。超过行数或大小限制时整批失败，**不会静默截断**。

本版本用于本机单进程部署。任务状态存本机文件，运行中任务归属存内存，不适用于多 worker / 多实例；升级时应把状态与并发控制放入共享任务系统。当前无后台调度、CDC、增量水位或快照自动清理；重复导入会生成新目录，请按保留策略清理历史快照。

## 4. 核对后接入现有数仓

快照成功只表示源表读取与保存成功，不表示已经通过业务质量校验。接入接口的 CSV 是“规范化契约”：字段映射把公司源字段改成 ETL 需要的标准英文列名，8 个实体都必须导出，不能只导出 orders。可从 `/api/sources/contracts` 获取每个实体的必需列。

### 按公司数据运行 ETL/ADS

1. 将 `D:\DataMind\backend\dataset.example.json` 复制为 `D:\DataMind\backend\dataset.local.json`，填入公司显示名、地区、币种代码及名称、Intl locale、业务时区和业务规则。CNY 配置示例已经采用“商品金额 + 运费”、排除 `canceled` / `cancelled` / `unavailable`。取消状态比较会 trim 并忽略大小写。此版本的固定 GMV 计算列是规范化 `price + freight_value`；如公司还需扣退款、优惠或税费，需先扩展 DWD 公式后再使用，不能只改说明文案。
2. 把成功导入任务的完整目录作为 ETL 输入。快照需包含 `orders`、`order_items`、`customers`、`products`、`sellers`、`order_payments`、`order_reviews` 和 `product_category_name_translation` 对应的 8 个 CSV，并映射好订单、客户、品类、金额、运费和卖家字段。`customer_unique_id` 应映射为公司业务认可的去重用户标识；若没有该字段，请不要把 `customer_id` 误当跨订单去重用户。
3. 用独立数据库前缀运行四层 ETL/ADS：

```powershell
cd D:\DataMind
D:\python3.12.10\python.exe etl\run_all.py --input-dir "D:\DataMind\data\ingestion\<成功 job_id>" --database-prefix datamind_company
```

外部快照必须传 `--database-prefix`，并且不能用默认的 `datamind`。上例只会写入 `datamind_company_ods`、`datamind_company_dwd`、`datamind_company_dws`、`datamind_company_ads`；同一隔离前缀内重复跑会覆盖该组公司库。默认不带参数运行仍指向 `data/raw` 和原 `datamind_*` Olist 库。

4. 确认 `dataset.local.json` 的 `database_prefix` 与运行时前缀一致，给后端只读账号授予新 ADS/DWS 库 `SELECT` 权限，然后重启后端。指标 API、SQL 查询白名单、问数业务字典和前端都从同一份数据集配置读取数据库前缀、名称、地区、币种、时区、GMV口径与排除状态。看板刷新后，核心指标、趋势和排行读新 ADS 表，币种格式和 Olist 标签同步更新。

5. 对账完成前，将新数据库前缀保留为隔离库。确认 GMV、客户数、状态筛选、首末日期、退款处理与公司报表一致后，再调整生产切换/回滚安排。

快照使用规范化字段，但不自动推断数据关系或修复数据值。切换前，需要检查：

1. orders.customer_id 能关联 customers.customer_id；客户去重所用 customer_unique_id 是否代表真实用户。
2. order_items 的订单、商品、卖家能关联；order_item_id 是现有 ETL 可转换的整数。
3. price 和 freight_value 的单位统一为主货币单位（例如元而非分）。当前 GMV 为 price + freight_value，不含退款处理。
4. 订单状态需要匹配现有规则：canceled / unavailable 被排除；其他状态是否应计入 GMV 要按业务确认。
5. 时间字段是约定时区的可解析日期时间；当前 ETL 使用 UTC 会话，业务时间口径需另行适配。
6. 非葡萄牙语品类可准备“原品类 → 看板显示名”的翻译表，不能直接套用 Olist 的翻译关系。
7. 当前 ODS 要求 8 个 CSV 全部存在。只导出订单不足以跑完整 ETL，也不能与旧 Olist 的其他表混用。

本项目暂不支持退款金额抵扣、税费/折扣、复杂多币种换算、CDC增量水位、任意订单模型或缺少品类/卖家等维度。外部快照必须经标准字段映射并用非默认前缀处理；不要复制覆盖 `data/raw`。本次没有连接公司源库，也没有运行 ETL 或覆盖现有 Olist 表。

## 验证

```powershell
cd D:\DataMind
D:\python3.12.10\python.exe backend\test_sources.py
```

测试使用假数据库连接与临时目录，覆盖鉴权、白名单、字段注入、映射、预览、只读事务、CSV 转义、限流量、失败清理、重复任务和凭据脱敏，不读取或修改业务表。实际业务库仍需填写连接配置后进行连接与数据核对。
