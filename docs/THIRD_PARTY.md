# 第三方项目与授权

本项目（群伴 · WeChat Group Agent，MIT）里集成了一个外部开源游戏前端，它的授权与 MIT 不同，单独说明：

## 云隐修仙录（yunyin-xiuxian）

- 仓库：<https://github.com/setube/yunyin-xiuxian>
- 作者：谦君（setube）
- 授权：**CC BY-NC 4.0（署名 — 非商业性使用 4.0 国际）**
- 全文：<https://creativecommons.org/licenses/by-nc/4.0/legalcode>
- 集成位置：`frontend/yunyin/`（由上游源码 `npm run build` 生成的静态产物），通过 `/rpg/yunyin/` 提供访问
- 改动：未修改其游戏逻辑，仅做生产构建并作为子路径挂载；构建时使用仓库自带 `vite.config.ts`（`base: './'`）

### 使用限制（重要）

1. **必须署名**：转载、分发、二次开发时保留原作者信息与本授权说明。
2. **禁止商业使用**：不得用于售卖、付费会员、广告变现等商业用途。
3. **本仓库根目录的 MIT LICENSE 不覆盖 `frontend/yunyin/` 目录**，该目录内容仍适用 CC BY-NC 4.0。
4. 如需商用，需要单独联系原作者取得授权，或替换为自己的实现。

## 群伴自己的代码

除 `frontend/yunyin/` 外，本仓库其余代码与素材按根目录 `LICENSE`（MIT）授权。
