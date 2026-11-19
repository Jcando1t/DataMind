# DataMind 前端

React + Vite 数据分析工作台，包含经营总览、品类分析、卖家分析和智能问数。金额使用巴西雷亚尔（BRL）。

## 环境依赖

- Node.js 20.19+ 或 22.12+
- Python 后端启动与配置见 `D:\DataMind\docs\项目进度总结.md`
- 为兼容启用 Windows 应用控制策略的环境，构建使用 Rollup 和 esbuild 的 WebAssembly 实现，不需要原生 `.node` 扩展。

## 本地运行

```powershell
cd D:\DataMind\frontend
npm install
npm run dev
```

Vite 将 `/api` 和 `/health` 代理到 `http://localhost:8000`。请先启动 FastAPI 后端；浏览器默认访问 `http://localhost:5173`。

## 构建

```powershell
npm test
npm run build
```

`npm run dev` 会启动 Vite 开发服务；当前工作区已验证地址为 `http://127.0.0.1:5173/`。预览构建可运行 `npm run preview`，Vite 也会把 `/api` 与 `/health` 代理至本地后端。

## 页面交互与范围

- 经营总览：全周期指标，月度 GMV / 订单数 / 客单价切换；年份筛选仅作用于趋势图。
- 品类分析：查看全量品类，支持名称检索、分页及带入问题到问数页。
- 卖家分析：查看接口返回的 Top 20，支持卖家 ID、城市和州检索。
- 智能问数：支持图表、明细分页和 SQL 展开；切换工作台页面保留当前会话。刷新浏览器后会话重置，不提供持久化历史。
- 布局覆盖 320px 手机到桌面；尊重减少动态效果偏好，并提供键盘焦点与导航标签。

设计参考与验收记录：`D:\DataMind\docs\前端重设计-设计依据.md`。
