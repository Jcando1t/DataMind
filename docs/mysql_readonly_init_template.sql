-- ============================================================
-- DataMind MySQL 只读查询账号初始化模板
-- ============================================================
-- 用途：创建智能问数 / 指标接口专用只读账号 datamind_ro，
--       仅授予 7 张 ADS/DWS 授权表的 SELECT 权限。
-- 安全约束：
--   * 密码用占位符 '<READONLY_PASSWORD>'，请在执行前替换；不要把真实密码写进本文件。
--   * 不使用 GRANT ALL、不授予 FILE、不授予任何写权限、不扩大到 ODS/DWD 库。
--   * 本模板只创建/授权账号，不删除或覆盖已有账号；若账号已存在请跳过 CREATE USER。
--   * 与 ETL 写入账号（各脚本顶部配置，通常 root）完全分离，互不影响。
-- 执行身份：需具有 CREATE USER / GRANT 权限的 MySQL 管理员账号。
-- ============================================================

-- 1) 创建账号（若已存在会报错，可改用 ALTER USER 轮换密码）
CREATE USER IF NOT EXISTS 'datamind_ro'@'localhost' IDENTIFIED BY '<READONLY_PASSWORD>';

-- 2) 仅授予必要表的 SELECT 权限（7 张）
GRANT SELECT ON `datamind_ads`.`ads_gmv_overview`     TO 'datamind_ro'@'localhost';
GRANT SELECT ON `datamind_ads`.`ads_gmv_trend`        TO 'datamind_ro'@'localhost';
GRANT SELECT ON `datamind_ads`.`ads_category_rank`    TO 'datamind_ro'@'localhost';
GRANT SELECT ON `datamind_ads`.`ads_top_sellers`      TO 'datamind_ro'@'localhost';
GRANT SELECT ON `datamind_dws`.`dws_daily_trade`      TO 'datamind_ro'@'localhost';
GRANT SELECT ON `datamind_dws`.`dws_daily_active_users` TO 'datamind_ro'@'localhost';
GRANT SELECT ON `datamind_dws`.`dws_category_summary` TO 'datamind_ro'@'localhost';

-- 3) 刷新权限
FLUSH PRIVILEGES;

-- 4) 复核（应只看到 USAGE + 7 条 SELECT，无 FILE / 写权限）
SHOW GRANTS FOR 'datamind_ro'@'localhost';

-- ============================================================
-- 说明：
-- * 白名单唯一来源为 backend/data_dictionary.py，须与本模板的 7 张表保持一致；
--   agent/sql_guard.py 从该数据字典加载白名单，执行层再独立校验一次。
-- * 只需首次部署执行；账号轮换时用 ALTER USER ... IDENTIFIED BY '<NEW_PASSWORD>'，
--   并同步更新 agent/.env 的 DATAMIND_QUERY_PASSWORD，无需改本模板或重启数据库。
-- * 本模板不含真实密码；执行时替换占位符后请妥善保管含密码的 agent/.env。
-- ============================================================
