/**
 * PlantOmics Studio - omics-drs 模块前端
 *
 * 为模块的主菜单项提供前端实现。
 * 在 PlantOmics Studio 内通过 module.js 来扩展前端界面。
 * 完整的 DRS 分析流程前端将在后续版本实现。
 */

const MODULE_ID = 'omics-drs';

/**
 * 模块注册
 * 由主前端框架在加载模块时自动调用
 */
export function register(studio) {
  console.log(`[${MODULE_ID}] 模块前端已加载`);

  // 注册 DRS 分析页面路由
  studio.registerRoute({
    id: 'drs',
    path: '/drs',
    component: () => import('./DRSPage.vue'),
    meta: {
      moduleId: MODULE_ID,
      title: 'DRS 分析',
      icon: 'rna',
    },
  });

  // 注册项目类型扩展
  studio.registerProjectType('drs', {
    name: 'Direct RNA Sequencing',
    icon: 'rna',
    description: 'ONT 直接 RNA 测序分析 - 不经反转录和 PCR 扩增',
  });
}
