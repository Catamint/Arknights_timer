# data/ — 游戏资源解包目录

本目录存放《明日方舟》客户端的解包产物，**体积太大不入库**（.gitignore 已排除，
仅保留本说明）。克隆仓库后需要自行解包填充，工具的部分功能依赖这些产物。

## 目录结构（解包后）

| 子目录 | 内容 | 谁在用 |
|---|---|---|
| `anon/` | exportRaw 数据表（`base_*`、`zz_hot_*`）及可选 raw CAB 伪解包目录 | `extract_tables.py` 的输入 |
| `tables/` | 从 anon 提取的数据表 bin（`character_table*`、`enemy_handbook_table*`、`skill_table*` 等）+ `effect_frames.json` | 运行时敌人/干员名称与属性数据库 |
| `battle/` | 战斗 prefab（`enm_pfb_*`、弹道 `[uc]projectiles`） | 生效帧提取 |
| `chararts/`、`charpack/` | 我方干员 spine 动画 / 参数 | 生效帧提取 |
| `refs/arts/` | 敌人 spine 动画（`enm_art_*`） | 生效帧提取 |
| `arts/`、`audio/`、`avg/`、`ui/` 等 | 其余全量资源 | 浏览/分析用，运行时不依赖 |

## 解包步骤

参照 github.com/isHarryh/Ark-Unpacker 

```
1.资源准备

无论您是想要使用我们的发行版本还是源代码来解包明日方舟的游戏资源，您都需要先获取到明日方舟的资源文件。明日方舟是基于 Unity 开发的游戏，它的游戏资源会全部打包到一种 AssetBundle文件（后缀名 .ab，下简称“AB文件”，但少数情况下后缀名是 .bin）中。
📱 安卓端资源获取（展开阅读）

下面将以 Android 系统 为例讲述如何获取到明日方舟的 AB 文件。安卓端的游戏资源有 2 个部分：

    一部分是通过安装包（.apk）提供的，从明日方舟官网将其下载到本地后，使用压缩文件查看工具打开（后缀名改成 .zip 后打开），然后把里面的 assets/AB/Android 文件夹解压出来；

    另一部分是通过热更新提供的，首先确保您的安卓手机上的明日方舟更新到了最新版本，然后（推荐使用 USB 数据线）将手机存储的 Android/data/com.hypergryph.arknights/files/Bundles 文件夹复制到电脑上（重命名为 Android(2)）。

    [!TIP] 对于使用模拟器的用户，可在模拟器中使用文件共享功能，将上述路径的文件夹直接复制到电脑上。若您熟悉 ADB 工具，也可以使用 ADB 来复制这些文件。

至此，我们的目录结构大致如下：

你的目录
├─Android
└─Android(2)

最后，将 Android(2) 文件夹里的内容复制到 Android 中，并覆盖同名文件，就能得到完整的游戏资源。在这之后，您就可以使用我们的程序来解包其中的游戏资源了。

当然，您也可以将 Android 里的部分文件夹复制出来进行处理，以解包特定的资源。
💻 PC 端资源获取（展开阅读）

下面将以 Windows 系统 为例讲述如何获取到明日方舟的 AB 文件。步骤如下：

    前往明日方舟官网下载并安装鹰角启动器。
    在鹰角启动器中，下载明日方舟 PC 端。随后启动游戏，并完成游戏内热更新。
    关闭游戏并返回鹰角启动器，点击右下方的菜单按钮，进入“游戏设置”，随后在“游戏安装路径”选项中点击“打开文件目录”按钮。
    在 Arknights_Data 文件夹中，StreamingAssets/AB/Windows 是 PC 端游戏的初始资源，PersistentData/Bundles 是热更新资源。
    参照安卓端的资源获取思路进行资源的整合即可。


为了便于您找到特定资源的 AB 文件位置，我们整理并列出了各个子目录储存的资源的内容，浏览此文档以查看详情。注意，此文档的内容可能不是最新。
```

1. **找到游戏 AB 文件**（PC 官服示例，其他服/模拟器路径类推）：
   ```
   <游戏目录>/Arknights_Data/StreamingAssets/AB/Windows/anon/
   ```
2. **用 ArknightsStudioCLI 以 `exportRaw` 模式解出数据表**：
    分别把基础包与热更包输出到 `data/anon/base_<日期>/` 和
    `data/anon/zz_hot_<日期>/`。`exportRaw` 文件可保持平铺的 `.dat` 布局，
    不需要改名或伪装成 CAB。
3. **提取数据表**（仓库根目录，需 Python 环境）：
   ```bash
   python extract_tables.py
   ```
   扫描 `data/anon/` 中平铺的 `.dat`/`.bin`/`.bytes` exportRaw 文件及有效的 raw CAB，
   按表名前缀识别并合并 base+hot，输出数据表到 `data/tables/`。
   Unity SerializedFile 格式的全零头 CAB 会被跳过。AB 文件名 hash 后缀随游戏版本变化，
   脚本按表名前缀匹配，**版本更新后重新跑即可，无需改代码**。
4. **（可选）提取动作生效帧**（需系统 Python + UnityPy）：
   ```bash
   python ark_parser/extract_effect_frames.py
   ```
   输出 `data/tables/effect_frames.json`。依赖步骤 2 中解出的 `battle/`、
   `chararts/`、`charpack/`、`refs/arts/`。不做此步则详情页「生效帧」tab 为空，
   其余功能不受影响。

## 运行时最低需求

- 敌人名称：`data/tables/enemy_handbook_table*.bin`
- 干员名称：`data/tables/character_table*.bin`（或 `ark_parser/char_names.json`）
- 缺表时工具会退化为显示游戏内 ID（如 `enemy_1045_hammer`），扫描与数值读取不受影响。

## 版本更新后

游戏大版本更新只需重做步骤 2-3（和可选的 4）：覆盖解包到新版本 AB，
重跑 `extract_tables.py`。若内存扫描偏移失效，另需刷新逆向偏移——
见 `tools/enemy_health/update_from_unpack.py`（从新版 `Ark_data/dump.cs` 重新生成）。
