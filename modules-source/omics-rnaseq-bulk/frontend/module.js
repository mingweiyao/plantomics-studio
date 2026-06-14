/**
 * omics-rnaseq-bulk 模块前端 bundle
 *
 * 这个文件由主程序在运行时通过 /modules/<id>/frontend.js 加载。
 * 主程序会注入 window.PlantomicsSDK 供这个模块使用。
 *
 * 子批次 3.1 是占位:只导出基础信息,3.2 才填实页面。
 */
export default {
  id: "omics-rnaseq-bulk",
  version: "1.0.0",
  // 模块自己注册的路由(menu_items 中 route 字段对应的页面)
  routes: {
    "/upstream": null,    // 待 3.2 实装
    "/deg": null,
    "/enrichment": null,
    "/wgcna": null,
  },
};
