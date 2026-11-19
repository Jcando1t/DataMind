# -*- coding: utf-8 -*-
# 依赖：Python 3.10～3.12、PySpark 3.5.3（pip install pyspark==3.5.3）、JDK 11（本机 C:\jdk11）。
# JDBC：MySQL Connector/J 8.4.0，首次运行由 Spark 自动下载。
# jar 下载：https://repo.maven.apache.org/maven2/com/mysql/mysql-connector-j/8.4.0/mysql-connector-j-8.4.0.jar
# 离线运行：设置 MYSQL_JDBC_JAR 环境变量为上述 jar 的绝对路径。
# Windows 请配置 JAVA_HOME；如 Hadoop 本地文件系统报错，配置匹配的 HADOOP_HOME。
# 补充总体、月度及卖家汇总，防止 ADS 累加品类订单数或每日用户数而重复计数。
import os
import sys
from pathlib import Path

from pyspark.sql import SparkSession, functions as F

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))
from datamind_config import database_name, load_dataset_config

DATASET_CONFIG = load_dataset_config()

# 数据库配置区：四个脚本使用相同配置。密码优先读环境变量 DATAMIND_ETL_PASSWORD，
# 默认值仅供本地开发，部署/开源环境请务必用环境变量覆盖。
MYSQL_HOST = "localhost"
MYSQL_PORT = 3306
MYSQL_USER = "root"
MYSQL_PASSWORD = os.environ.get("DATAMIND_ETL_PASSWORD", "123456")
MYSQL_DRIVER = "com.mysql.cj.jdbc.Driver"
MYSQL_JDBC_JAR = os.environ.get("MYSQL_JDBC_JAR")
APP_NAME = "DataMind-DWS"

def create_spark():
    builder = (
        SparkSession.builder.appName(APP_NAME)
        .master("local[*]")
        .config("spark.sql.session.timeZone", DATASET_CONFIG["timezone"])
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.sql.shuffle.partitions", "8")
    )
    if MYSQL_JDBC_JAR:
        builder = builder.config("spark.jars", str(Path(MYSQL_JDBC_JAR).resolve()))
    else:
        builder = builder.config(
            "spark.jars.packages", "com.mysql:mysql-connector-j:8.4.0"
        )
    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    return spark


def jdbc_url(database):
    return (
        f"jdbc:mysql://{MYSQL_HOST}:{MYSQL_PORT}/{database}"
        f"?useSSL=false&serverTimezone={DATASET_CONFIG['timezone']}"
    )


def create_database(spark, database):
    # 使用同一个 JDBC 驱动建库，无需额外安装 Python MySQL 客户端。
    properties = spark._jvm.java.util.Properties()
    properties.setProperty("user", MYSQL_USER)
    properties.setProperty("password", MYSQL_PASSWORD)
    connection = spark._jvm.com.mysql.cj.jdbc.Driver().connect(
        jdbc_url(""), properties
    )
    try:
        statement = connection.createStatement()
        try:
            statement.executeUpdate(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        finally:
            statement.close()
    finally:
        connection.close()


def read_table(spark, database, table):
    return spark.read.jdbc(
        url=jdbc_url(database),
        table=f"`{table}`",
        properties={
            "user": MYSQL_USER,
            "password": MYSQL_PASSWORD,
            "driver": MYSQL_DRIVER,
            "fetchsize": "10000",
        },
    )


def write_table(frame, database, table):
    frame = frame.persist()
    try:
        count = frame.count()
        frame.coalesce(4).write.jdbc(
            url=jdbc_url(database),
            table=f"`{table}`",
            mode="overwrite",
            properties={
                "user": MYSQL_USER,
                "password": MYSQL_PASSWORD,
                "driver": MYSQL_DRIVER,
                "batchsize": "5000",
            },
        )
        print(f"✓ {table} 写入完成，共 {count} 行", flush=True)
    finally:
        frame.unpersist()


def build_tables(spark):
    dwd = database_name("dwd", DATASET_CONFIG)
    orders = read_table(spark, dwd, "dwd_orders")
    items = read_table(spark, dwd, "dwd_order_items")
    # 通过订单关联统一过滤已取消、不可用订单及其明细。
    trade = items.join(orders.select("order_id", "order_date"), "order_id", "inner")

    daily_trade = (
        trade.groupBy("order_date", "category_english")
        .agg(
            F.countDistinct("order_id").alias("order_count"),
            F.sum("total_price").alias("gmv"),
        )
        .withColumn("avg_order_value", F.round(F.col("gmv") / F.col("order_count"), 2))
    )
    daily_active = orders.groupBy("order_date").agg(
        F.countDistinct("customer_unique_id").alias("active_user_count")
    )
    category_summary = trade.groupBy("category_english").agg(
        F.countDistinct("order_id").alias("total_orders"),
        F.sum("total_price").alias("total_gmv"),
        F.round(F.avg("price"), 2).alias("avg_price"),
        F.countDistinct("seller_id").alias("seller_count"),
    )

    # 全局及月度订单数均从订单粒度计算，一笔多品类订单只计一次。
    # 清洗后无有效商品明细的订单仍计入订单数，其 GMV 为零。
    order_amounts = trade.groupBy("order_id").agg(
        F.sum("total_price").alias("_order_gmv")
    )
    order_facts = orders.join(order_amounts, "order_id", "left").withColumn(
        "_order_gmv",
        F.coalesce(F.col("_order_gmv"), F.lit(0).cast("decimal(28,2)")),
    )
    overview = (
        order_facts.agg(
            F.coalesce(F.sum("_order_gmv"), F.lit(0).cast("decimal(38,2)")).alias("total_gmv"),
            F.countDistinct("order_id").alias("total_orders"),
            F.countDistinct("customer_unique_id").alias("total_customers"),
            F.min("order_date").alias("data_period_start"),
            F.max("order_date").alias("data_period_end"),
        )
        .withColumn(
            "avg_order_value",
            F.when(F.col("total_orders") > 0,
                   F.round(F.col("total_gmv") / F.col("total_orders"), 2))
            .otherwise(F.lit(0).cast("decimal(38,2)")),
        )
        .select(
            "total_gmv", "total_orders", "avg_order_value", "total_customers",
            "data_period_start", "data_period_end",
        )
    )
    # 补齐缺失月份；环比中的上一期必须是上一自然月。
    bounds = orders.agg(
        F.trunc(F.min("order_date"), "month").alias("_start"),
        F.trunc(F.max("order_date"), "month").alias("_end"),
    )
    months = (
        bounds.filter(F.col("_start").isNotNull())
        .select(F.explode(F.sequence("_start", "_end", F.expr("INTERVAL 1 MONTH"))).alias("_month"))
        .select(F.date_format("_month", "yyyy-MM").alias("year_month"))
    )
    monthly = (
        order_facts.withColumn("year_month", F.date_format("order_date", "yyyy-MM"))
        .groupBy("year_month")
        .agg(
            F.sum("_order_gmv").alias("monthly_gmv"),
            F.countDistinct("order_id").alias("monthly_orders"),
        )
    )
    monthly = months.join(monthly, "year_month", "left").fillna(
        {"monthly_gmv": 0, "monthly_orders": 0}
    )
    seller_summary = trade.groupBy("seller_id").agg(
        F.max("seller_city").alias("seller_city"),
        F.max("seller_state").alias("seller_state"),
        F.sum("total_price").alias("total_gmv"),
        F.countDistinct("order_id").alias("total_orders"),
    )
    return {
        "dws_daily_trade": daily_trade,
        "dws_daily_active_users": daily_active,
        "dws_category_summary": category_summary,
        "dws_trade_overview": overview,
        "dws_monthly_trade": monthly,
        "dws_seller_summary": seller_summary,
    }


def main():
    spark = create_spark()
    try:
        tables = build_tables(spark)
        database = database_name("dws", DATASET_CONFIG)
        create_database(spark, database)
        for table, frame in tables.items():
            write_table(frame, database, table)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

