# -*- coding: utf-8 -*-
# 依赖：Python 3.10～3.12、PySpark 3.5.3（pip install pyspark==3.5.3）、JDK 11（本机 C:\jdk11）。
# JDBC：MySQL Connector/J 8.4.0，首次运行由 Spark 自动下载。
# jar 下载：https://repo.maven.apache.org/maven2/com/mysql/mysql-connector-j/8.4.0/mysql-connector-j-8.4.0.jar
# 离线运行：设置 MYSQL_JDBC_JAR 环境变量为上述 jar 的绝对路径。
# Windows 请配置 JAVA_HOME；如 Hadoop 本地文件系统报错，配置匹配的 HADOOP_HOME。
import os
import sys
from pathlib import Path

from pyspark.sql import SparkSession, functions as F
from pyspark.sql.window import Window

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
APP_NAME = "DataMind-DWD"

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
    ods = database_name("ods", DATASET_CONFIG)
    orders_raw = read_table(spark, ods, "ods_orders")
    customers_raw = read_table(spark, ods, "ods_customers")
    products_raw = read_table(spark, ods, "ods_products")
    items_raw = read_table(spark, ods, "ods_order_items")
    sellers_raw = read_table(spark, ods, "ods_sellers")
    translation = (
        read_table(spark, ods, "ods_product_category_name_translation")
        .select(
            "product_category_name",
            F.col("product_category_name_english").alias("category_english"),
        )
        .dropDuplicates(["product_category_name"])
    )

    # 客户表没有更新时间，以该 customer_id 最近一次原始下单时间判断新旧。
    latest_purchase = orders_raw.groupBy("customer_id").agg(
        F.max(F.to_timestamp("order_purchase_timestamp")).alias("_last_purchase")
    )
    customer_window = Window.partitionBy("customer_unique_id").orderBy(
        F.col("_last_purchase").desc_nulls_last(),
        F.col("customer_id").desc(),
    )
    customers = (
        customers_raw.join(latest_purchase, "customer_id", "left")
        .withColumn("_rn", F.row_number().over(customer_window))
        .filter(F.col("_rn") == 1)
        .drop("_rn", "_last_purchase")
        .withColumnRenamed("customer_city", "city")
        .withColumnRenamed("customer_state", "state")
    )

    # 在客户去重前建立映射，保证同一用户的历史 customer_id 仍能识别。
    customer_ids = customers_raw.select(
        "customer_id", "customer_unique_id"
    ).dropDuplicates(["customer_id"])
    order_window = Window.partitionBy("order_id").orderBy(
        F.col("order_purchase_timestamp").desc_nulls_last(),
        F.col("customer_id").desc(),
        F.col("order_status").desc(),
    )
    statuses = [status.lower() for status in DATASET_CONFIG["exclude_order_statuses"]]
    eligible_orders = orders_raw
    if statuses:
        eligible_orders = eligible_orders.filter(
            ~F.lower(F.trim(F.col("order_status"))).isin(*statuses)
        )
    orders = (
        eligible_orders
        .withColumn(
            "order_purchase_timestamp", F.to_timestamp("order_purchase_timestamp")
        )
        .withColumn("_rn", F.row_number().over(order_window))
        .filter(F.col("_rn") == 1)
        .drop("_rn")
        .withColumn("order_date", F.to_date("order_purchase_timestamp"))
        .withColumn("order_year", F.year("order_purchase_timestamp"))
        .withColumn("order_month", F.month("order_purchase_timestamp"))
        .join(customer_ids, "customer_id", "left")
    )
    # 缺少订单日期或用户标识会导致汇总丢数，写入前明确报错。
    invalid_order = orders.filter(
        F.col("order_id").isNull()
        | F.col("order_date").isNull()
        | F.col("customer_unique_id").isNull()
    ).limit(1).count()
    if invalid_order:
        raise ValueError("订单存在空 order_id、无效下单日期或无法关联的客户")

    products = (
        products_raw.join(translation, "product_category_name", "left")
        .withColumn("category_english", F.coalesce("category_english", F.lit("unknown")))
        .select(
            "product_id", "product_category_name", "category_english",
            F.col("product_weight_g").cast("decimal(18,2)").alias("product_weight_g"),
        )
        .dropDuplicates(["product_id"])
    )
    # 保留卖家地区，DWS 和 ADS 即可只从各自上一层读取。
    sellers = sellers_raw.select(
        "seller_id", "seller_city", "seller_state"
    ).dropDuplicates(["seller_id"])
    items = (
        items_raw.withColumn("order_item_id", F.col("order_item_id").cast("int"))
        .withColumn("price", F.col("price").cast("decimal(18,2)"))
        .withColumn("freight_value", F.col("freight_value").cast("decimal(18,2)"))
        .filter(F.col("price") > 0)
        .join(products.select("product_id", "category_english"), "product_id", "left")
        .withColumn("category_english", F.coalesce("category_english", F.lit("unknown")))
        .withColumn("total_price", F.col("price") + F.col("freight_value"))
        .join(sellers, "seller_id", "left")
    )
    if items.filter(F.col("total_price").isNull()).limit(1).count():
        raise ValueError("有效商品明细缺少 freight_value，无法计算 total_price")
    return {
        "dwd_orders": orders,
        "dwd_order_items": items,
        "dwd_customers": customers,
        "dwd_products": products,
    }


def main():
    spark = create_spark()
    try:
        tables = build_tables(spark)
        database = database_name("dwd", DATASET_CONFIG)
        create_database(spark, database)
        for table, frame in tables.items():
            write_table(frame, database, table)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

