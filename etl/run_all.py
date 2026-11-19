# -*- coding: utf-8 -*-
# 依赖：Python 3.10～3.12、PySpark 3.5.3（pip install pyspark==3.5.3）、JDK 11（本机 C:\jdk11）。
# JDBC jar：https://repo.maven.apache.org/maven2/com/mysql/mysql-connector-j/8.4.0/mysql-connector-j-8.4.0.jar
# 密码通过环境变量 DATAMIND_ETL_PASSWORD 提供（默认 123456 仅供本地开发），并确保 MySQL 已启动且账号有建库权限。
# 运行方式：python D:\DataMind\etl\run_all.py

import os
import argparse
from pathlib import Path
import subprocess
import sys

ETL_DIR = Path(__file__).resolve().parent
ROOT_DIR = ETL_DIR.parent
sys.path.insert(0, str(ROOT_DIR))
from datamind_config import DEFAULTS, load_dataset_config
SCRIPTS = (
    ETL_DIR / "ods" / "load_ods.py",
    ETL_DIR / "dwd" / "build_dwd.py",
    ETL_DIR / "dws" / "build_dws.py",
    ETL_DIR / "ads" / "build_ads.py",
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="运行 DataMind 四层 ETL。")
    parser.add_argument("--input-dir", help="规范化快照目录（仅接受 ODS 约定的 8 个 CSV）")
    parser.add_argument("--database-prefix", help="目标数据库名前缀，例如 datamind_company；快照导入必须使用隔离前缀")
    args = parser.parse_args(argv)
    dataset_config = load_dataset_config()
    if args.input_dir and not args.database_prefix:
        parser.error("使用 --input-dir 时必须提供 --database-prefix，避免覆盖当前 Olist 数仓")
    if args.input_dir and args.database_prefix == "datamind":
        parser.error("外部快照不能覆盖默认 datamind 数仓，请使用隔离前缀，例如 datamind_company")
    if args.database_prefix and not args.input_dir and args.database_prefix != "datamind":
        parser.error("指定公司数仓前缀时必须同时指定规范化快照 --input-dir")
    if not args.input_dir and dataset_config["database_prefix"] != "datamind" and dataset_config["raw_dir"] == str(Path(DEFAULTS["raw_dir"]).resolve()):
        parser.error("当前配置为隔离数仓前缀，但输入目录仍是默认 Olist 原始目录；请使用 --input-dir 和 --database-prefix")

    # 避免 Windows 默认编码无法输出勾号，同时让 Spark 使用当前 Python。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYSPARK_PYTHON"] = sys.executable
    environment["PYSPARK_DRIVER_PYTHON"] = sys.executable
    # JAVA_HOME 路径不能含空格，使用复制到无空格路径的 JDK
    environment["JAVA_HOME"] = r"C:\jdk11"
    environment["HADOOP_HOME"] = r"D:\DataMind\hadoop"
    if args.input_dir:
        input_dir = Path(args.input_dir).resolve()
        if not input_dir.is_dir():
            parser.error(f"快照目录不存在：{input_dir}")
        environment["DATAMIND_RAW_DIR"] = str(input_dir)
    if args.database_prefix:
        environment["DATAMIND_DATABASE_PREFIX"] = args.database_prefix
    for script in SCRIPTS:
        print(f"开始运行：{script}", flush=True)
        try:
            subprocess.run(
                [sys.executable, str(script)],
                cwd=str(ETL_DIR.parent),
                env=environment,
                check=True,
            )
        except subprocess.CalledProcessError as error:
            print(
                f"错误：{script.name} 执行失败，退出码 {error.returncode}，流水线已停止。",
                file=sys.stderr,
                flush=True,
            )
            return error.returncode if error.returncode > 0 else 1
        except OSError as error:
            print(f"错误：无法启动 {script}：{error}", file=sys.stderr, flush=True)
            return 1
    print("✓ ODS → DWD → DWS → ADS 全部执行完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

