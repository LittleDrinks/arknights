# 2026-10-18 目录整理与验证

训练目标保持暂停，本次只整理结构、迁移文件并验证接口与网页。

## 当前结构

项目根现为一个Git仓库，保留原实验代码历史。game通过.gitmodules引用上游GitHub仓库，以gitlink固定在6ab2468279e926d107788f63d7fe219b85f67b4a；工作树干净，依赖、下载素材、缓存、录像及定制页面均已移到外部。

主源码按adapters、strategies、studies、evaluation、replay分类；其中251个原源码/配置/说明文件保持基线字节，153个旧结果文件退出当前Git跟踪，原Git历史仍可恢复。旧的相对导入及哈希锁通过scripts/ark.py生成的忽略运行目录兼容；对原研究结果不作重新命名、内容改写或成功状态升级。

运行结果统一放results，只有目录.gitkeep被跟踪。历史源码快照也在results/archive下，VS Code已排除结果、运行环境的Git扫描及搜索。Node依赖和Python环境在runtime，Node标准包解析入口node_modules是被忽略的链接。

## 真实迁移证据

- 原始文件 **98,893个**，总逻辑字节 **43,778,582,480**。同盘rename后逐文件核对device/inode/size/mtime/type/link，全部一致。
- 保全receipt：results/migrations/layout-20261018/preservation.json；移动日志、旧新路径表及所有迁移脚本也在同一目录。
- 虚拟环境8个路径脚本在保全验证后单独修复，原件保留于venv-script-originals。迁移后Python、pip及Torch均可运行。
- 两个回放索引HTTP返回内容逐字节相同，45个episode/169场录像保留。
- 原规则版本不变，干净game下的8条环境响应（reset、6个step、close）共249290bytes，与迁移前逐字节相同，SHA256=3290ebbe11b05814575df5d6bacb9eeb73b521396ffe6be1a9be21ad4ff10ebe。
- 外部网页适配服务继续监听38181。浏览器实际AI3/R14：播放、暂停、拖到3642/3643末帧成功；0页面异常、0素材缺失、0console错误。未在game中写入任何补丁。

## 检查

- 迁移/恢复回归：服务器11项通过，1.655秒；覆盖目标冲突、源祖先链接、悬空链接、首次/末次rename中断、receipt前后中断、临时文件完整及部分写入后的恢复，重复恢复、输出约束。
- 原PPO数值回归：服务器15项通过，0.904秒。
- scripts/check_layout.py：game锁定、源码哈希、gitlink、结果忽略规则全部通过。
- 新PPO及回放CLI帮助、虚拟环境pip、旧路径解析器均通过。
- 详细机器结果在results/tests/；此次未运行训练、正式benchmark或新的胜率评测。

## 恢复与后续

历史文档、报告、checkpoint metadata中的绝对路径仍是原记录，不能据此判断文件丢失。使用scripts/resolve_legacy.py解析；新的程序应使用当前根README中的统一入口。

新的教师批次依然未冻结、未完成全量语义审计。移动目录不会使其变成可训练数据。studies/pending/scale-bc依然是待独立验收代码。下一阶段训练策略须由用户恢复后再决定。
