# 原始数据集放置说明

本目录存放 Olist 巴西电商公开数据集的 9 个 CSV 文件，**数据文件不随仓库分发**（体积约 121MB，且数据集采用 CC BY-NC-SA 4.0 许可）。

## 下载方式

前往 Kaggle 下载并解压到本目录：

**[Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)**

## 所需文件清单（9 个）

| 文件 | 内容 |
|---|---|
| `olist_orders_dataset.csv` | 订单主表 |
| `olist_order_items_dataset.csv` | 订单明细 |
| `olist_order_payments_dataset.csv` | 支付信息 |
| `olist_customers_dataset.csv` | 客户信息 |
| `olist_products_dataset.csv` | 商品信息 |
| `olist_sellers_dataset.csv` | 卖家信息 |
| `olist_order_reviews_dataset.csv` | 订单评论（评论语义洞察功能依赖） |
| `olist_geolocation_dataset.csv` | 地理信息 |
| `product_category_name_translation.csv` | 品类名葡英翻译 |

放置完成后，按根目录 README 的「快速开始」运行 ETL 即可。评论语义洞察功能的本地索引会在首次提问时自动构建，无需额外操作。

## 许可

数据集版权归属 Olist，依据 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) 使用，请勿用于商业用途。
