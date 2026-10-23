/* 桌宠只用 MMDAnimationHelper 的 IK/grant 解算（physics:false），不需要真物理。
   真正的 MMDPhysics 依赖 ammo.js 全局，这里给一个空实现，仅保证模块能 import。 */
export class MMDPhysics {
  constructor() {}
  reset() {}
  update() {}
}
