/**
 * PlantOmics Studio - omics-tail-iso-seq 模块前端
 *
 * 为模块的主菜单项提供前端实现。
 * 在 PlantOmics Studio 内通过 module.js 来扩展前端界面。
 * 完整的前端实现将在后续版本完成。
 */

const MODULE_ID = 'omics-tail-iso-seq';

/**
 * 模块注册
 * 由主前端框架在加载模块时自动调用
 */
export function register(studio) {
  console.log(`[${MODULE_ID}] 模块前端已加载`);

  // 注册 Tail Iso-seq 分析页面路由
  studio.registerRoute({
    id: 'tail-iso-seq',
    path: '/tail-iso-seq',
    component: () => import('./TailIsoSeqPage.vue'),
    meta: {
      moduleId: MODULE_ID,
      title: 'Tail Iso-seq 分析',
      icon: 'dna',
    },
  });

  // 注册项目类型扩展
  studio.registerProjectType('tail_iso_seq', {
    name: 'Tail Iso-seq',
    icon: 'dna',
    description: '全长转录本 Iso-seq 分析 - Pychopper + Pinfish + poly(A)',
  });
}
