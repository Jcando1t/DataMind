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
APP_NAME = "DataMind-ODS"

RAW_DIR = Path(DATASET_CONFIG["raw_dir"])
SOURCE_TABLES = {
    "olist_orders_dataset.csv": "ods_orders",
    "olist_order_items_dataset.csv": "ods_order_items",
    "olist_order_payments_dataset.csv": "ods_order_payments",
    "olist_customers_dataset.csv": "ods_customers",
    "olist_products_dataset.csv": "ods_products",
    "olist_sellers_dataset.csv": "ods_sellers",
    "olist_order_reviews_dataset.csv": "ods_order_reviews",
    "product_category_name_translation.csv": "ods_product_category_name_translation",
}

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


def main():
    # 所有字段按字符串落地，保留邮编前导零，ODS 不做类型转换和清洗。
    missing = [str(RAW_DIR / name) for name in SOURCE_TABLES if not (RAW_DIR / name).is_file()]
    if missing:
        raise FileNotFoundError("缺少原始 CSV：" + "、".join(missing))
    spark = create_spark()
    try:
        database = database_name("ods", DATASET_CONFIG)
        create_database(spark, database)
        for filename, table in SOURCE_TABLES.items():
            frame = (
                spark.read.option("header", "true")
                .option("inferSchema", "false")
                .option("encoding", "UTF-8")
                .option("multiLine", "true")
                .option("quote", '"')
                .option("escape", '"')
                .option("mode", "FAILFAST")
                .csv((RAW_DIR / filename).as_posix())
            )
            write_table(frame, database, table)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

