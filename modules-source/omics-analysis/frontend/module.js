/**
 * omics-analysis 模块前端 bundle
 *
 * 这个文件由主程序在运行时通过 /modules/<id>/frontend.js 加载。
 * 主程序会注入 window.PlantomicsSDK 供这个模块使用。
 *
 * 下游分析页面(/downstream)实际由主程序内置实现(AnalysisHome,已在核心路由
 * m/omics-analysis/downstream 注册),这里只导出模块基础信息与路由占位,
 * 保持与其它模块一致的打包结构(打 deb 时会被一并拷入)。
 */
export default {
  id: "omics-analysis",
  version: "1.0.0",
  // 模块自己注册的路由(menu_items 中 route 字段对应的页面)
  routes: {
    "/downstream": null, // 实际页面由主程序内置 AnalysisHome 提供
  },
};
