# 游戏素材与授权

## 动作闯关（/rpg/action）用到的第三方素材

| 素材 | 文件 | 来源 | 授权 |
| --- | --- | --- | --- |
| 角色 / 怪物 / 地形 / 道具 | `frontend/assets/sprites/*.png` | [Free asset pack · OpenGameArt](https://opengameart.org/content/base-platformer-pack) | **CC0 1.0**（公有领域，可商用、可修改、无需署名） |

用到的具体文件：

- 角色：`Wizard.png`（玩家·修仙者）、`Oldman.png`（石傀/老者）
- 怪物：`Slime.png`（山魈）、`Ghost.png`（阴灵）、`Vampire.png`（妖将 BOSS）
- 地形：`Ground*.png`、`Grass*.png`、`Bush*.png`
- 道具：`Crystal.png`、`Coin.png`、`Heart.png`

CC0 不要求署名，这里仍然写明来源，方便后续替换或追溯。

## 自己画的部分

- 天空、山峦、云带、月晕、竹影、花瓣、剑气/雷符特效、UI 与 HUD 全部是 Canvas 代码绘制（`frontend/rpg-action.html`）。
- 表情包（`data/stickers`、`docs/assets/stickers`）来自 [dsh-nekowhale-stickers](https://www.npmjs.com/package/dsh-nekowhale-stickers)。

## 想换素材怎么办

1. 把新的 PNG 放进 `frontend/assets/sprites/`；
2. 改 `frontend/rpg-action.html` 顶部 `ART` 里对应的文件名；
3. 确保授权允许（CC0 最省事），并把来源补进这张表。
