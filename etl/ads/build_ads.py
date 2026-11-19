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
APP_NAME = "DataMind-ADS"

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
    dws = database_name("dws", DATASET_CONFIG)
    overview = read_table(spark, dws, "dws_trade_overview").select(
        "total_gmv", "total_orders", "avg_order_value", "total_customers",
        "data_period_start", "data_period_end",
    )
    monthly = read_table(spark, dws, "dws_monthly_trade")
    categories = read_table(spark, dws, "dws_category_summary")
    sellers = read_table(spark, dws, "dws_seller_summary")

    month_window = Window.orderBy("year_month")
    trend = (
        monthly.withColumn("_previous_gmv", F.lag("monthly_gmv").over(month_window))
        .withColumn(
            "mom_growth_rate",
            F.when(
                F.col("_previous_gmv").isNotNull() & (F.col("_previous_gmv") != 0),
                F.round(
                    (F.col("monthly_gmv") - F.col("_previous_gmv"))
                    / F.col("_previous_gmv") * 100, 2,
                ),
            ).cast("decimal(18,2)"),
        )
        .drop("_previous_gmv")
        .orderBy("year_month")
    )
    # 首月及上月 GMV 为零时，环比无定义，保留 NULL。
    category_window = Window.orderBy(
        F.col("total_gmv").desc(), F.col("category_english").asc()
    )
    category_rank = (
        categories.crossJoin(overview.select(F.col("total_gmv").alias("_all_gmv")))
        .withColumn("rank", F.row_number().over(category_window))
        .withColumn(
            "gmv_ratio",
            F.when(F.col("_all_gmv") != 0,
                   F.round(F.col("total_gmv") / F.col("_all_gmv") * 100, 2))
            .otherwise(F.lit(0)).cast("decimal(18,2)"),
        )
        .select("rank", "category_english", "total_gmv", "total_orders", "gmv_ratio")
        .orderBy("rank")
    )
    seller_window = Window.orderBy(
        F.col("total_gmv").desc(), F.col("seller_id").asc()
    )
    top_sellers = (
        sellers.withColumn("rank", F.row_number().over(seller_window))
        .filter(F.col("rank") <= 20)
        .select("rank", "seller_id", "seller_city", "seller_state", "total_gmv", "total_orders")
        .orderBy("rank")
    )
    # MySQL 表没有固有行序；消费排行和趋势时应按 rank 或 year_month 排序。
    return {
        "ads_gmv_overview": overview,
        "ads_gmv_trend": trend,
        "ads_category_rank": category_rank,
        "ads_top_sellers": top_sellers,
    }


def main():
    spark = create_spark()
    try:
        tables = build_tables(spark)
        database = database_name("ads", DATASET_CONFIG)
        create_database(spark, database)
        for table, frame in tables.items():
            write_table(frame, database, table)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

