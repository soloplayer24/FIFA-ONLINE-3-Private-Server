[![SEASON UPGRADE 25, 26, WC18, SERIE A](docs/season-upgrade-25-26-wc18.svg)](https://www.mediafire.com/file/ettm1y9u3s0r3t2/FO3ID-Season-25-26-LIVE-WC18-SerieA-client.zip/file)

# FO3ID Private Server

[English](README.md) | [Bahasa Indonesia](README.id.md) | **简体中文** | [ไทย](README.th.md) | [Tiếng Việt](README.vi.md) | [한국어](README.ko.md)

> 本文译自 [README.md](README.md)。如有出入，以 README.md 为准。

**新增：** FIFA Online 4 的 25 LIVE 和 26 LIVE 球员卡作为两个新赛季（属性按 +0 计，使用 FO4 的特性和赛季徽章），26 LIVE 为默认赛季；另有 WC18：2018 世界杯大名单中最好的 300 名球员（+1 时每张卡为其赛事最终评分 - 5）。见 `tools/fo4_season.py`。

这是 **FIFA Online 3 印尼版**（Garena，客户端版本 654420）的本地私服，用 Python 编写。Garena 已于
2020-06-30 关闭官方服务器；本项目重新实现了客户端所连接的后端，让游戏可以在一台电脑上离线游玩。

> 与 EA、Spearhead 或 Garena 无关。本仓库**不包含任何游戏文件**（没有客户端、数据库、素材或音乐），
> 你需要自己安装 FO3 ID 客户端。本项目**不**包含、修改或绕过客户端的反作弊（XIGNCODE3 / HackShield），
> 这不在项目范围内。仅用于游戏保存与研究。

游戏界面是印尼语。下文中的 Liga、Cup、Market、Tukar、Tim inti、Tim Cadangan、Item saya、Gudang 等
都是游戏里显示的界面名称，保留原文以便对照。

## 已可用的功能

已在真实客户端（印尼语界面）中测试：

- 登录 → 选择 / 创建经理（可从 501 个授权俱乐部中挑选）→ 大厅（FREEROAM）。
- **Single → Liga**（完整赛季：赛程、比赛、积分榜、奖励、升级）和 **Cup**（抽签、小组赛、淘汰赛、
  加时、点球大战）。
- **Market**（买 / 卖；球员卡价格取决于评分和强化等级：fifaaddict 上同评分所有球员卡的价格中位数）、
  **Upgrade**（强化等级 +1…+10）、**Tukar** 和 **Tukar Scout** 球员交易（所有 Market 球员卡，
  不只是本地区授权的）、Tim inti（主力队）/ Tim Cadangan（替补队）、阵容和战术保存。
- **Shop**（商城）所有标签页（球员卡包、消耗品、礼包、MP Shop、月卡礼包和连锁礼包、特别奖励），
  开卡包时随机强化等级，Item saya（我的物品）、Bag（礼物箱）、每日登录奖励。
  **Gudang**（Item saya > Gudang，仓库）：可把卡包和消耗品放进去再取回。
  额外卡包：**All Season +8**（任意赛季，+1…+8），以及 '07 和 fifaaddict 收录的每个赛季各一个卡包。
- 始终拥有 VIP + 官方网吧（IGR）特权，经理删除 / 恢复，'17 赛季球员卡。
- 阵型列表（俱乐部自己的阵型 + 游戏的 33 种常用阵型，读取自客户端的阵型表），以及经理 / 球员等级：
  每场比赛后获得 EXP，球员 LV.1-23（每级所有属性 +1），经理 LV.1-100。
- 游戏音效和 EA Trax 菜单音乐。`tools/eatrax_song.py` 可以把你自己的歌放到第一首，但之后游戏会在
  比赛结束后崩溃（见下文的已知问题）。
- 网页管理后台 http://127.0.0.1:8088/（EP、球员、卡包、礼物、每日奖励、比赛奖励、比赛设置、经济、AI 聊天、商城、日志），可切换语言
  （issue #15）：Bahasa Indonesia、English、简体中文、ไทย、Tiếng Việt、한국어；浏览器会记住你的选择。
- 比赛中显示真实的俱乐部球衣（kit id = `teamid << 5 | type`；旧的 `<< 10` 会显示成棋盘格）。
- 开启 **team color**（球队加成）时比赛可以踢完下半场（以前赛季加成会在中场时崩溃），之后显示 EXP 和
  奖励界面。

已添加并通过测试，但尚未在客户端中验证：

- **team color** 加成本身：首发 11 人中来自同一俱乐部的球员获得 OVR 加成（4-5 人 +1，最高 10-11 人
  +4），同一国家或同一赛季（5-6 人 +1，最高 9-11 人 +3）；每名球员只取最高的一项。
- **Opsi > Kontrol**（键盘和手柄按键）可以保存：服务器总是发送游戏自带的默认按键布局，因此该界面可以
  应用并保存修改（以前修改会丢失）。
- 比赛设置界面中的**球衣选择**（每方主场 / 客场）会用于比赛。
- **选择经理**界面显示俱乐部身价最高的球员，穿着俱乐部球衣（以前显示游戏默认的巴塞罗那球员）。
- 修复的崩溃：启动公告弹窗（网页 URL 为空）、Beginner's Guide 联赛教程（缺少脚本）。
- **强化成功率**：按目标等级的基础成功率，乘以 Upgrade 标签页根据材料卡等级显示的
  "Rasio bonus keberhasilan" 标签（Kosong x1、Low x1.1、Normal x1.25、High x1.5）。从 +5 及以上
  强化失败会掉一级。
- **Top 200 卡包**：额外卡包现在给出该赛季最好的 200 张卡（"17 Top 200"、"07 Top 200" 等）；
  **All Season Top 300 +8** 给出任意赛季评分 81 及以上的卡。
- **用 +0 卡强化**（issue #5）：俱乐部自己的 +0 卡可以 100% 保住目标卡的等级（+4 用 +0 仍是 +4），
  +10 卡也可以使用；被保留的卡占用被消耗卡的阵容位置时会离开 Tim Cadangan。
- **Upgrade 2.5**（客户端较新的强化界面，`features...enhancement.v2_5`）：
  - **强化点数**：强化失败会获得点数；"Gunakan poin" 消耗点数换取 +10%；可在 Shop > Paket 免费购买更多
    （Upgrade Point 100 / 500 / 1.000）；
  - **加成道具** Enhancement Boost Gold / Silver / Bronze（+15 / +10 / +5%），在 Shop > Dapat dikonsumsi
    中免费；
  - 永久的**强化活动**（目标 +1…+9 时 +10%，`GET /events`）；
  - "Coba pengembalian lagi" 可以用点数找回因失败而掉的等级。
- **Tukar v2**（`trade2`）：交易按价值（TP）分为 15 级，每级有最高出价等级；标签页显示其他经理"昨天"
  在该等级得到了什么；传奇赛季球员卡按 1.5 倍计算；没有每日或价值上限。Tukar Scout 仍可同时使用。
- **Tukar Scout**：客户端另外两个球探，Scout Khusus（活动；传奇赛季任务）和 VVIP Scout（最佳报价；
  10 亿 EP 任务）。去掉了 Scout Amatir，使列表正好放下 5 行（以前列表可以滚动时，完成的任务会点亮
  别的球探卡，或者都不亮）。
- **强化等级球队加成**（客户端中的 "Upgraded Team"）：强化等级 +2、+5 或 +8 及以上的首发球员；
  5-6 / 7-8 / 9-11 人分别给 +1 / +2 / +3、+2 / +3 / +4 和 +3 / +4 / +5 OVR。单一等级的加成
  （"+3 club" … "+10 Golden summit"）需要 11 名首发全部达到该等级或更高：+4（club +3/+4）、
  +5（silver +5…+7）、+6（Golden passion +8/+9）、+7（Golden summit +10）。球员在最佳俱乐部 / 国家 /
  赛季加成之上，再获得他最好的强化加成。
- **客户端中有名称的所有 team color**（现有 188 个）：
  - 27 个俱乐部（Real Madrid … Jeonbuk）：Single team 的档位，加上游戏提示中该俱乐部自己的加成，
    例如 Barcelona MF agility +3；
  - 30 个国家：Satu Negara 的档位，加上例如 Spanyol MF ball control +2；
  - 使用游戏中名称的赛季加成（World Best 最高 +5、World Legend、2014 WC All-Star 等）；
  - 约 75 个特殊加成：'14 WC 和 EC 各国家、nation BEST、MSN / BBC Trio、Arsenal Invincibles、
    Galácticos、LEGENDS、TOP 100、Power Header、New Star 等。

  原本的加成数值和球员名单是服务器数据，已经丢失。球员名单来自球员数据库（赛季、俱乐部、点名的球员、
  属性限制）；加成数值来自游戏中仍保留的一行提示。未加入：[Test] 加成、"+2~7"、"14 WC Single Nation"
  和四个重复的俱乐部条目。
- **Formasi**（阵型）打开时不再提示 "Tidak cukup pemain"（球员不足）（它会加载服务器从未发送过的预设
  阵容 1）。首发中的空位（issue #7，同一球员的两张首发卡互相强化）会由替补补上，替补席也不会出现同一
  球员的第二张卡。每名首发都会得到 `starting` 数据（积极性和场上位置）：没有它游戏会把他显示在替补席。
- **Tim > Peran** 会保存队长和每个定位球主罚者（issue #8：缺少 `PUT /game/my/team/captain` 和
  `/game/my/team/kickers/<0-4>`）。五个主罚位置（左侧角球、右侧角球、任意球、点球、任意球 2）保持不变；
  空位由一名首发补上。
- **Tim > Locker room**（更衣室）可用（issue #13）：球衣款式和袖子、纹身、护腕、手套（门将：39 种手套
  设计）、球袜、护膝、按品牌分类的 614 双球鞋，以及球衣号码（选用一个号码会与原佩戴者互换）。之前缺少
  物品表 `/static/db/LOCKERROOMITEM`，所以每个列表都是空的，球鞋面板也会出错。球员的外观保存在球员卡上，
  Item saya 中有一张永久更衣室券，游戏需要它才能在比赛中显示这些外观。护腕、手套和护膝只提供客户端
  自带的模型（各一个）。
- **管理后台的商城控制**（issue #10）：商城所有标签页（球员卡包、消耗品、礼包、月卡礼包、推荐、
  MP Shop、特别奖励、连锁礼包）。可以新建卡包（按赛季、前 N 名、OVR、最高强化等级、位置、联赛）、
  随机箱、EP 卡、礼包和月卡礼包，可使用客户端 147 个商城图标中的任意一个。自带商品可以修改、隐藏或
  设置固定价格，整个商城也可以设为免费或收费。修改保存在 `gamedata/shop.json`。
- **Simulasi Liga**（联赛模拟）券（物品 2001，Shop > Dapat dikonsumsi）：Liga 的 Simulasi 按钮不用踢就
  直接给出比赛结果，每场比赛一张券。模拟界面会按结果播放比赛的进球、射门、扑救和红黄牌（之前
  `eventlist` 为空，所以显示 0-0），之后的结果界面显示比分、球员评分和数据、控球率和 MOTM（以前是空白），
  然后是 EP 奖励并回到 Liga 菜单。该按钮会统计 Item saya 中的券数，以前因为没有这个物品而一直不可用。
  可在管理后台新建券（类型"联赛模拟券"）。
- **赛前阵容界面**（Liga 和 Cup，issue #16）：换位、换人和阵型修改现在会保留并保存到俱乐部（之前
  `PUT /match/<id>/side/<side>/squad` 没有处理程序，所以每次修改都会自动撤销）。阵型列表有了内容，
  体力条显示为满（比赛之间不保留疲劳）。
- **球队 FW / MF / DF 能力值与球员一致**（issue #18）：在阵容界面和大厅中，它是球员列表所显示能力值的平均值
  （球员所在位置的能力值，加上强化等级、等级和队伍颜色）。之前用的是球员卡的基础能力值，大约低 12。
  比赛前（联赛、杯赛、排位）的球员列表不计入队伍颜色，所以那里的能力条也不计入；比赛中加成照常生效。
- **联赛记录各标签页**（issue #19）：积分榜可以显示了（之前缺少 `league2difficulty` 表，标签页出错；这张表现在也决定赛季
  结束时的升级和降级）。我的记录列出本赛季已踢比赛中每名球员的出场、评分、进球、助攻、传球、抢断和扑救，综合记录显示
  本赛季最佳 11 人和各项数据领先者。只统计自己的球员，模拟比赛不增加球员数据。
- **Formasi 中的 Squad A / B / C**（issue #20）：阵型按钮左边的框用来选择三套保存的阵容之一（之前是空的，因为服务器
  没有发送阵容数量）。每套阵容保存自己的首发、阵型和角色。
- **控制器和视角设置在比赛中保持**（issue #21）：在 Opsi > Konfirmasi 之后，游戏会从服务器回复中读回保存的选项，
  之前回复里没有，所以每场比赛都会回到登录时的选项（键盘、旧视角）。比赛中按 F3-F9 切换视角（F5 = Co-op）。
- **Ganti tim（更换球队）显示俱乐部和你的球员**（issue #23）：这个道具的俱乐部界面现在显示联赛、德比对手、主场、
  建立年份和容纳人数，并显示你的球员穿新俱乐部球衣的样子，以及出生日期、身高和体重。之前服务器的俱乐部信息里没有
  这些内容，没有球衣时 3D 球员也不会显示。
- **视角高度和缩放可以调整**（issue #24）：Opsi > Kamera setting 里的 Customise 刻度（高度和缩放；Pro 视角为缩放、速度和
  摆动）之前没有数值也无法调整，因为服务器发送的视角设置是空的。现在从 10 开始（最大 20）。
- **Tim lain 里有所有国家队**（issue #24）：International 联赛（列表中最后一个国家）有 40 支国家队，包括意大利、
  英格兰、德国、西班牙和法国。国家队阵容取自每名球员的国家队关联；之前只列出球员没有授权俱乐部的国家队。
- **结果画面的球员位置**（issue #26）：比赛结束后，结果画面的球场把所有球员都叠在球门处，因为游戏把每名球员都
  报告为门将。现在服务器按阵容位置给每名球员设置位置。
- **赛后奖励卡**（issue #26）：奖励画面的卡片之前是空的。现在会发三张卡：你选的那张给 EP（1,000 到 10,000）、
  球员包（Semua Pemain 或 '14 Top 200）或 Lucky EP 道具，另外两张显示本可以得到的奖励。
- **选秀卡包和经验胶囊**（issue #27）：Shop > Pak pemain 出售选秀卡包（"06 Draft Pick 3"、"2017 Draft Pick 5"、
  "World Legend Draft Pick 3"）。"Gunakan" 打开挑选球员界面，默认填入该赛季最值钱的卡；挑选该赛季的 N 张卡
  （可按名字、联赛或俱乐部搜索），然后打开其中一张。Shop > Dapat dikonsumsi 出售经验胶囊（1,000、5,000、
  Max = LV.23、一个限 LV.10 及以下、一个限 2017 卡）：选一名球员，他获得这些经验。选秀界面的请求
  （`/item/draftpack/<id>/players/<n>`、`/transfer/price/default/<grade>`）和胶囊道具类型之前都没有。
  管理页面可以再做更多（卡包的 "Draft" 栏位，道具类型 "EXP Capsule"）。
- **Multiple > Rank：其他经理和真实比赛**（issue #30）：排位画面之前因服务器没有发送段位、
  奖励和天梯记录而停在 UI 错误。现在可以打开，排行榜由虚拟经理填满：前 3 名（VS Manager Top）
  和 20 人的好友排名，每人都有名字、俱乐部、段位和战绩，战绩会随着他们一天中的比赛不断变化。起初有 100 人，
  另有 1,850 人，之后每天有 0-3 名新人加入，最多 2,000 人（issue #36 中翻倍）。每人都有自己的打法（控球、反击、高位逼抢、长传冲吊、摆大巴或全力进攻，
  各有不同的阵型、战术和跑位），穿着自己俱乐部的球衣，球员是与其评分相当的随机卡牌，可来自任何俱乐部和赛季，
  每张卡都有各自的强化等级（从新手的 +1 到 +6 甚至更高）和等级。他们每天都会买卖卡牌、强化卡牌
  （从 +5 起强化失败会掉一级）并提升等级。Rank 1 on 1 和 VS Manager 会与评分最接近你的 5 名经理之一
  踢一场真实比赛：阵容画面、比赛设置，然后是对方由游戏 AI 控制的完整比赛，之后是结果、奖励和你新的排位评分，
  并会出现在排位画面的面板、记录窗口（最近的比赛、赛季走势图）和 Info Manager 的比赛列表中。
  比赛中对手会按自己的性格在比赛聊天里发言（开球时、双方进球时、射门偏出时、门将扑救时，以及你输入聊天时回复），
  回到排位画面时还会评论比赛结果；管理后台的 AI 聊天标签页可以关闭它或选择语言（印尼语、英语、越南语）。
  Messenger 现在也能打开了（之前它一直在等服务器从未发送过的通知列表），并根据好友的上线时段（08:00-02:00 WIB）
  显示 20 名好友在线、忙碌（正在踢 Rank 比赛）或离线；点好友的 Info 按钮可以查看他的资料和阵容。
- **Market 活起来了**（issue #30）：虚拟经理会买下你挂出的卡，"Dijual" 会在几分钟内自动变成
  "Terjual"（已售出），价格越低卖得越快，同时弹出 "terjual seharga" 售出提示，并收到一条写明买家的通知；
  Batal（取消）和 Ulang（重新上架）也都能用。除了 +1..+3 的库存，他们还会挂出自己的卡（也有其他强化等级），
  价格略低于市场价，Kuantitas（数量）每小时都会变。球员卡窗口的 Market Price 标签页会画出最近 30 天的价格走势。
- **经济设置**（issue #30）：管理后台新增的 Ekonomi（经济）标签页可以把游戏调成你想要的难度：球员和卡包的
  价格倍率、新经理的初始 EP、商城免费开关，以及动态 Market。在动态 Market 里，每名球员的价格每天都会
  浮动（上下最多约 13%），虚拟经理时不时会挂出便宜的卡，你挂卡时售价最高可以定到市场价的 105%
  （买家要更久才会出手），所以低买高卖能赚到 EP。预设有"轻松"（和以前一样：商城免费、价格固定）、
  "真实"（商城按卡的价值收费、开启动态 Market、初始 300,000 EP）和"困难"（价格 x2、初始 100,000 EP）。
  比赛获得的 EP 仍在"比赛奖励"标签页里设置。
  商城收费时，联赛模拟券的价格等于一轮比赛能拿到的奖励；开启动态 Market 时，卡包按卡在最高价时的价值收费
  （+13%），因为卡包可以留到任何一天再开。"严格规则"（困难下开启）让强化、交易和球探无法刷 EP：强化费用
  等于它平均增加的价值（恢复需要支付 EP），交易和球探的报价最多等于交出球员的价值。关闭时（轻松、真实）
  按游戏原本的规则：强化费用为球员卡价值的 2%，恢复使用强化点数，报价不设上限。
  不含 EP 的商品（强化加成、强化点数、经验胶囊、改名或换队）按基础价格乘以球员价格倍率收费，所以"困难"下价格翻倍。
  更改会立即作用于正在运行的游戏：服务器会通知游戏丢弃本次会话缓存的商店列表，商店标签页下次打开时就显示新价格；
  游戏启动后已经打开过的标签页会保留旧价格，直到重启游戏。商店的限量库存检查也不再弹出"Kesalahan UI telah terjadi"。
- **AI 聊天扩充十倍**（issue #30）：虚拟经理的聊天在每种语言里为每个时刻和每种性格各准备了 20 句，共 2,700 句：
  开球时的寒暄和足球话题，对进球、射失和扑救的反应，你打字时的调侃，以及对比赛结果的感想。所有句子都避开了
  游戏聊天过滤器会屏蔽的词（它也会屏蔽藏在更长单词里的词）。
- **虚拟经理按自己的风格组建阵容**（issue #30）：每位经理都有固定的阵容主题：接近其评分的任意球员卡；一支顶级俱乐部
  （皇马、巴萨、马竞、曼联、曼城、切尔西、利物浦、阿森纳、热刺、拜仁、多特蒙德、尤文图斯、米兰、国际米兰、罗马、
  那不勒斯、巴黎圣日耳曼）的现役或前球员；一个顶级国家（巴西、阿根廷、西班牙、德国、法国、英格兰、意大利、荷兰、
  葡萄牙、比利时、乌拉圭、克罗地亚）；游戏的某个团队颜色（一个卡片赛季或一组命名球员）；一位球星加上他的俱乐部
  或国家队队友；版本强势（meta）；或某一项属性（速度、身体、技术、传球、防守、射门、年轻球员、老派球员）。实力
  越强的经理越常走 meta 和团队颜色路线。主题只选取接近其评分的球员卡，所以不会让阵容变得更强。
- **赛前设置**（issue #31）：Multiple > Rank 的比赛设置界面现在可以用左右箭头切换球场（客户端中资源齐全的
  70 座球场）、时间（白天或夜晚）、天气（晴、雨、雪或阴天，夜晚选阴天则为黄昏）、比赛时长（6、8、10 或
  12 分钟）和比赛用球（9 种）。最后一次的选择会沿用到之后的比赛，包括 Liga 和 Cup；它们的赛前界面
  只显示这些选项，所以也可以在管理后台新增的"比赛设置"标签页里设定，还可以选"主队球场"或"随机"。
  服务器修正了客户端印尼语文本中的两个标签：白天原来显示为 "Waktu"（时间），阴天原来显示为
  "Malam"（夜晚）；不过 Liga 和 Cup 的界面仍把阴天显示为 "Salju"（雪）。
- **键盘和手柄各自保留按键预设**（issue #32）：Opsi > Kontrol 只保存一套预设，即当前所用设备的预设；
  切换设备时，另一种设备会回到它的第一套预设。现在服务器会记住每种设备最后使用的预设，并在按下
  Konfirmasi 后恢复（下次打开该界面时会显示出来；比赛从开球起就会使用它）。
  想在切换设备时立刻看到对应的预设，请在关闭游戏后运行一次 `python tools/control_presets.py`：它会修补客户端的
  Kontrol 界面（会保留备份；`--remove` 可恢复原版）。
- **经理菜单有内容了**（issue #33）：经理信息里显示的是你的记录而不再是"-"：联赛、杯赛和排位中的比赛、进球、胜场和
  连胜，联赛冠军和达到的难度，赢得的杯赛及奖金，买卖的球员卡和各自最贵的一张，游戏时间，以及你的简介和照片。
  这些数据从这个版本开始统计。经理排名会显示你最近 30 天的球队价值与 20 位好友平均值的对比、你在他们中的排名，
  以及每人与昨天相比的名次变化。
- **任务与成就**（issue #33）：Manager > Daftar pencapaian 里有徽章行（经理等级、比赛、胜场、强化、最高等级、
  球队价值、进球、排位胜场），每个成就有 5 档可领取的 EP、卡包和道具奖励，一整行完成后可佩戴其徽章；另有每日
  任务一行（比赛、获胜、进球、排位），每天 00:00 WIB 重置。顶栏的任务按钮会打开每周任务（每周一 00:00 WIB
  重置）并显示倒计时；完成的任务会奖励 GP，可在该窗口的奖励箱中兑换（EP、Lucky EP、卡包、强化加成、
  Kotak Emas）。服务器会统计你踢的比赛和成功的强化。管理后台新增的 Misi（任务）页可开关任务，设置语言（印尼语、
  英语、越南语）以及目标和奖励倍率；EP 奖励也会随经济页的球员价格变化。
- **开球时的裁判**（issue #33）：两名助理裁判取自一条空记录（游戏的裁判列表里没有该裁判），头部、头发和身体都是
  不存在的资源，所以在入场列队时显得畸形。现在服务器使用游戏里真实存在的裁判。
- **使用 FO3 越南版客户端的越南语解说**（issue #34）：关闭游戏后运行
  `python tools/commentary.py "<FO3 越南版客户端文件夹>"`。它会把越南版客户端的 `audiodata\speechdata\vie_vn`
  复制到本客户端英语解说（`eng_us`）旁边，并在 `data\loc\locale.big` 中把解说语言只设为越南语（会保留备份），
  之后游戏就从 `vie_vn` 读取解说。`--remove` 可恢复英语解说。
- **经理升级奖励**（issue #35）：经理从 2 级到 100 级的每一级都会发放 FO3 的升级奖励（fifaaddict 上的表格）：EP 加上
  道具，例如 Top 300、Top 100 和 Best 卡包、联赛 65+ 与 GK 65+ 卡包、联赛模拟券、换队券以及经验胶囊。奖励放进背包
  （"Hadiah naik level Lv.N"）；赛后界面会在升级弹窗中显示，信息图标会列出之后几级的奖励。服务器用不到的 FO3 道具
  （教练包、Red Ball、更衣室和奖励加成券、经验转移）不发放。EP 奖励随经济页的球员价格变化。
- **俱乐部**（issue #36）：Klub 可以用了。可以自己创建俱乐部（10,000 EP；"Klub telah dibuat" 之后的 "Konfirmasi"
  不再一直是灰色），也可以加入由虚拟经理经营的 60 个 AI 俱乐部之一，每个俱乐部都有会长、成员、等级、简介和新闻。
  没有俱乐部的经理会申请加入你的俱乐部（Manajemen > permohonan；俱乐部设为无需审核即可加入时会直接加入）。
  成员按各自的上线时段在线，积累活跃点数，在留言板发帖，并在聊天窗口的 Klub 标签页里聊天：回复你的问候和消息，
  欢迎新成员，为你的比赛结果祝贺或打气，使用的是管理后台设定的聊天语言（印尼语、英语、越南语）。
  每周的俱乐部点数会带来比赛加成（经理经验、球员经验、EP），你自己一天攒满 30 点时会往背包发 1,000 EP。尚未实现：
  俱乐部比赛、俱乐部设施和俱乐部排名。
- **与 AI 的友谊赛**（issue #36）：Multiple > Persahabatan 的按钮又出现了。Quick match 和 Random match
  会为你找到一位虚拟经理；自建房间可以打 vs COM（AI 选择器中选的俱乐部和等级，Amateur 到 Legendary），
  也可以和你从列表中邀请的好友比赛。结果在 Info Manager 中计为友谊赛，不计入排位评分，对手也会像在排位中那样聊天。
- **Rank 与 King of the Hill 的加时赛和点球大战**：90 分钟后打平（Rank 1 vs 1、Rank Manager、King of the Hill）
  会进入加时赛，仍平则点球大战；点球获胜在评分、战绩和比赛奖励中都算胜利。
- **King of the Hill**（issue #36）：Esports 地区联赛每月一个赛季。从印尼的 19 个地区中选一个；
  该地区的排行榜由当地的虚拟经理组成，按下 Mulai 会与其中一位评分接近你的 King of the Hill 评分的经理比赛。
  奖励界面会显示赢得或失去的点数。
  管理后台的比赛设置页可以把地区换成印度尼西亚、新加坡/马来西亚、泰国或越南（越南语名称）的城市，或在那里按每行“区域: 城市, 城市”写的自定义地区（例如在 Miền Bắc 加上 Sa Pa）。
- **登录公告图片**（issue #36）：管理后台的 Pengumuman（公告）页可以为登录时的"公告"弹窗上传 JPG 或 PNG 图片（适配
  800×720 的弹窗，带预览），也可以删除它。
- **截至本赛季的俱乐部经历**（fifamvp 的 PR #39）：球员信息的特性（Karakteristik）页显示球员卡的俱乐部经历
  （`features.seasoncare`）。FO3 自带的经历止于 2017 年左右，且只在 22-24 赛季的卡上显示；现在有经历的球员的所有卡都会显示，
  管理后台的 Aset 页（俱乐部经历，或 `python tools/club_history.py fetch`）从 Wikidata 获取截至当前赛季的经历（年份、
  俱乐部、租借；FO3 没有名称的俱乐部会在客户端中加上名称）。然后点击“数据库 > 应用到游戏”。
- **在管理后台下载缺失的图片**（issue #36）：Aset（图片）页会在后台从 fifaaddict.com 下载缺失的球员头像（每张卡的赛季头像和球员头像）、
  俱乐部队徽、联赛标志和教练照片（一次一种；可随时停止、随时继续）。它运行的是 `tools/fa_faces.py`，
  该工具现在也支持 `--leagues`。比赛设置界面的 "Default ball" 会显示 EA SPORTS 的球，小国旗改用大国旗图片，
  Cup 界面也有了奖杯和皮肤。
  fifaaddict 没有（或拒绝）的球员头像改从 FC Online（FO4）获取（相同的 EA 球员 ID；服务器新增的球员用 fifaaddict 上其 FO4 卡的头像），扫描覆盖数据库中
  所有球员卡，被拒绝时会等待更久后继续而不是停止，“下载全部缺失的图片”一次获取所有类型。
- **使用语言包时的奖励名称**（issue #36）：使用越南语语言包时，成就列表的每个奖励前都显示 "undefined"，还会出现
  UI 错误，因为服务器只用印尼语和韩语给奖励道具命名。现在客户端的每种语言都有名称了。
- **与 AI 进行 Exhibition**（issue #36）：Multiple > Exhibition 可以开房了。用箭头或 Pilih Team 选择授权俱乐部，
  从列表邀请好友（虚拟经理，会带着自己选的俱乐部加入并立即准备），然后按 Siap，用两家俱乐部的真实阵容比赛。
  每天 5 场；你的球员卡不会从这些比赛获得 EXP。Live Center 打开时不再出现那两个 UI 错误。
- **来自 EA FC 每周最佳阵容（Team of the Week）的 Live Boost**：Tim > Live Boost 显示本周的
  Team of the Week（来自 fut.gg；服务器在周三公布后获取新一周，需要联网）。这些球员的所有球员卡本周每项属性 +5
  （能力值 +5）：阵容、转会市场和比赛中都生效。只列出 FO3 有的球员（和 26 LIVE 新球员），没有女足球员卡。拥有其中
  11 人（+1 及以上）每周可领一次 100,000 EP（Terima）。
- **每周任务编辑器**（管理后台 Misi 页）：每周抽取的任务列表（类型、目标、GP、可用 {0} 表示目标的自定义名称）、
  每周数量（最多 6 个）和任务窗口的 GP 奖励箱（最多 5 个），并预览本周和下周的任务。本周已完成的任务保持完成。
- **游戏内每日登录奖励**：当天的奖励仍在首次登录时进入背包；现在会弹出通知，点击该条目会打开显示第几天和物品的弹窗。
- **游戏时长奖励**：今天的游戏时长达到 10、30、60 分钟时发放奖励（每天从 00:00 WIB 起算；超过 20 分钟的空闲不计），
  进入背包并显示同样的通知。目标在管理后台的 Daily reward 页设置。
- **全局聊天**：聊天窗口的 Channel 页可以用了：你的消息会显示，在线的虚拟经理会不时回复并自己聊天（使用 AI 聊天页的语言）。
- **全局聊天中的足球话题**：Channel 页中的虚拟经理会讨论当天的足球：某地区正在上升、与足球有关的 Google 搜索，以及
  该语言的 Google 新闻标题（X、Instagram 和 TikTok 需要登录，Reddit 拒绝服务器），每天获取一次。每位经理喜欢在新闻
  提到时谈论自己的俱乐部、球星或国家，其他人会回应。全球新闻始终开启；印度尼西亚、
  马来西亚、越南、泰国和韩国在管理后台的 AI 聊天页手动勾选，那里也会显示当前话题。
- **管理后台的排位奖励**：Match 页可修改各段位的赛季奖励（主、副）和每周胜场档位，排位界面也会显示。现在奖励会真正
  进入背包：每周档位通过排位界面上游戏自带的弹窗发放，赛季奖励在下个赛季开始时发放（评分最高模式的主奖励，以及
  其他每个已玩模式的副奖励）。
- **管理后台的 King of the Hill 奖励**：比赛设置页可修改每周奖励（1-7、8-14、15-21 或 22-28 日这一周的胜场或场次
  目标，以及胜场足够时的段位奖励）和赛季奖励（胜场足够时月底的段位奖励，以及地区排名奖励）。King of the Hill
  界面会显示这些奖励；你参加的一周或一个赛季结束后，打开 King of the Hill 时会出现奖励界面，达成的奖励进入背包。
- **完整联赛**（issue #40）：一个 Liga 赛季包含我所在联赛中阵容完整的所有俱乐部（Premier League 20 家俱乐部、
  38 轮；最多 24 家），而不是 10 家。按名次的 EP 和经验按球队数调整。
- **FO3 ID 缺少的俱乐部**：AC Milan、Genoa、Udinese、Bologna、Cagliari 和 Sampdoria 回到游戏（球衣仍在游戏文件中；
  `tools/fo4_season.py install` 按球衣读出的颜色添加俱乐部），并带完整的 26 LIVE 阵容（`fetch` 列出名单，`collect` +
  `import` 读取能力值）。这些球员原本放在国家队的 '17 卡回到俱乐部。Liga 的意甲现在有 19 家俱乐部，选择俱乐部界面
  也会显示。做法：fetch、collect + import、build，然后在关闭游戏时 install（或在管理后台点 Terapkan ke game）。
  原本放在 Rest of the World 的意大利俱乐部球员的旧 FO3 卡（如 Milan 的 Pirlo '06，共 322 张）也回到俱乐部；
  `restore` 可还原。
- **管理后台的球员卡与俱乐部数据库**（issue #38）：Database 页按赛季逐张编辑球员卡（spid = 赛季 × 1,000,000 +
  球员 ID；可按名字、spid 或球员 ID 搜索）：33 项属性、位置、惯用脚和逆足、花式技巧、攻防积极性、特性、俱乐部（转会）
  和 3D 外观（任意 3D 行的头部，例如另一张卡的脸；通用头部可改发型和发色、胡须、肤色、眼睛和体型，发色 0 = 金发；
  扫描头部保留自己的头发）。自建赛季：代码、名称和徽章，用其他卡的复制品填充（可给所有属性 +N），每个赛季都有自己的
  Top 200 卡包和团队色。球员卡修改写入 `gamedata/fifadb.sqlite` 和 `gamedata/card_edits.json`（原值和新值：Kembalikan
  可恢复，`tools/fo4_season.py build` 会重新应用），赛季写入 `gamedata/seasons.json`；Ekspor / Impor mod 用一个 JSON
  文件把这些全部搬到另一台服务器。游戏读取自己的数据库副本：关闭游戏，点击 Terapkan ke game（在后台运行
  `tools/fo4_season.py install`），然后运行 `play.bat`。该页还能替换客户端缓存中的俱乐部队徽（原队徽保存在
  `gamedata/crest_backup`），并可在球员卡窗口用自己的 PNG/JPG 或其他 spid / 球员 ID 的头像（来自缓存或 fifaaddict.com）替换球员头像，作用于该球员所有卡或仅本卡（issue #36；
  `gamedata/faces/p<id>.png`，优先于缓存中的头像；3D 脸型栏也可按球员名字查找头部）。球衣和新俱乐部还没有做。
- **用照片制作 3D 脸**（Database 页，球员卡窗口）：给通用脸的球员用正面特写照片，作为脸部皮肤贴到另一名已扫描
  球员的 3D 头上（供体：头型和头发随之而来，眼睛保留供体的）。在照片上点 11 个点（眼睛、鼻子、嘴角、下巴、额头、
  太阳穴、下颌），选择供体后点“制作”；“应用到游戏”把这个头放进客户端（`data_7.big`，从原版重新生成），用于该球员
  所有使用其球员 ID 行或通用头的卡。保存在 `gamedata/photo_heads`。在比赛镜头距离下与照片相像，光照来自照片，头型来自供体。
  “从头像获取”用球员的 FO3 头像（缓存中的，没有则用 fifaaddict.com 的）填入照片、点和供体：YuNet 人脸检测器（OpenCV Zoo，
  MIT；只下载一次到 `gamedata/yunet`）找出眼睛、鼻子和嘴，供体是肤色和头发（颜色和发型，按供体自己的头像）最接近、没有胡子的
  扫描头。Database 页的按钮会为俱乐部中所有有头像的通用脸球员这样做（没有 FO3 头像的新球员会被跳过）。
  肤色按头像对头像（供体自己的头像）比较，并与将带来的脸部贴图比较（打光的头像比贴图读起来更亮：以前深肤色球员会
  拿到浅肤色供体）；东亚/东南亚球员用这些国家的供体，其余用其他国家的（供体的眼睛会保留）；笑时露出的牙齿换成供体
  闭合的嘴唇。“用头像重新生成全部 3D 脸”会重做已有的脸。
- **经典 FO3 世界**（issue #38）：Database 页的 *Dunia game* 可选现代（AI 俱乐部、俱乐部选择和 This Season
  卡包使用 26 LIVE 卡，与之前相同；自建赛季也会出现在市场搜索中）或经典 FO3：所有俱乐部使用 FO3 原版 2017 阵容，
  This Season = '17，新增赛季（25 / 26 LIVE、WC18、自建赛季）不会出现在卡包、市场、交易或虚拟经理阵容中，只能通过
  管理后台发放。你的俱乐部和收藏不变，FO3 原有球员卡也不会被覆盖（新赛季是另外添加的）。保存在
  `gamedata/world.json`；重启（`play.bat`）后生效。
- **随时重新生成虚拟经理**：管理后台 Chat AI 页的 *Generate ulang* 会立即生成一批新的虚拟经理（名字、等级、
  俱乐部、阵容、打法、Rank 积分、地区、AI 俱乐部），ID 不变，好友、俱乐部和记录照常；*Kembalikan ke awal* 找回
  第一批。只有这个按钮会改变他们，每次启动都保持不变（`gamedata/vm_gen.json`）。
- **管理后台的自建团队色**：Team color 页可以新建团队色。成员卡用筛选条件选择（赛季、现在或曾经的俱乐部、国家、
  球员、最低 OVR、市场最佳 N 张卡；卡必须满足所有已填写的条件），并带一个加成（OVR 或一项属性，作用于所有位置或
  FW / MF / DF / GK），等级与内置特殊团队色相同（首发 5-6 / 7-8 / 9-11 人）。保存在 `gamedata/teamcolors.json`；
  下次登录起出现在游戏的 Team colour 列表中，无需重启。虚拟经理的阵容不使用自建团队色和自建赛季，模组文件也会带上它们。
- **教练团队（Staff）**（Tim > Manajemen Staff）：FO3 的教练系统重新启用。*Rekrut*（招募）：用 EP 购买教练包
  （Basic FW / MF / DF / GK / ALL、Pro、Premium、技术总监、Premium 体能），每天登录可免费领取一个 ALL 包，再踢
  3 场比赛可再领一个。*Staff yang dimiliki*（已有教练）：7 个职位框（FW、MF、DF、主教练、GK、体能教练、技术总监）；
  放在对应职位框的教练会提升该位置组首发球员的属性（体能教练、主教练和技术总监提升全部首发），阵容列表和比赛中都生效，
  合同为 50 场，可用 EP 续约。*Latihan*（训练）：把其他教练投入一名教练，提升其等级（0.5 到 5 星）和加成。
  *Pembinaan*（进修）：派教练出国 5-10 场比赛，更换或提升一项加成。名字和加成属性来自 fifaaddict 的 fo3staff
  列表；等级、价格和合同由服务器自定（Garena 的数据已遗失），EP 价格随经济页的球员价格变化。客户端只有 7 名教练的
  照片，`python -I tools/fa_faces.py --staffs` 会从 fifaaddict 补上其余照片。
  虚拟经理比赛时和你一样有球队颜色加成，并按等级拥有教练（每 12 级多一个职位框）。
  Issue #41：*Rekrut* 一次最多招募 10 名教练；管理后台的 Pak & Bag 页可给账号添加任意教练（0.5-5 星）并删除；
  教练包图片放在 `assets/externalAssets/shop/<icon>.png`（170×120：broall, brofw, bromf, brodf, brogk (Basic), silall (Pro), goldall (Premium), position (Technical Director), medical (Premium Medical), staffpack_2103 (free pack)），没有则显示教练照片；
  缺少第 11-13 包名称的语言包（泰语）会补上。
- **球员成长**（Tim > Manajemen pemain > Perkembangan，FO3 ID 关闭的 FO3 功能）：23 级球员卡用战术点逐次 +1 提升
  属性，每张卡最多 60 点；每步花费取决于属性值（60 以下 4 点到 140 以上 60 点，fifaaddict 的 FO3 成长表）。提升的
  属性在所有界面、球队评分和比赛中生效。战术点来自亲自比赛的数据（进球、助攻、传球、抢断、扑救……加上胜负，模拟
  不算），累计最多 1,200，赛后奖励界面会显示。"Reset poin taktik" 道具（Shop）可退回一张卡的点数。虚拟经理的
  球员卡也会成长，经理越强成长越多。
- **随机箱可以打开**（Kotak Platinum 等，issue #12）：箱子界面从 `staticdata.availables` 列出奖品，
  之前缺少这个字段，所以每个箱子在 "Gunakan"（使用）时都会失败。
- **新球员卡进入 Tim Cadangan**：从卡包、交易、Market、Bag 或管理后台得到的卡，在 Skuad 的 Tim 一栏显示
  "-"，可以直接用于 Tukar Scout。点卡上的按钮可把它移到 Tim inti（绿色 RES：18 人大名单之外的 Tim inti），
  Tim inti 可以容纳整个俱乐部（200 人）。管理后台可以一次把 Tim Cadangan 的球员全部移到 Tim inti。
- **Market 搜索**可以找到所有赛季的球员卡：游戏只搜索 `/static/roster` 中的赛季，而其中缺少 29 个有
  Market 球员卡的赛季（World Legend、World Best、'14 TOTS 等），所以例如 WL R. Gullit 从来搜不到。
  Market 筛选的赛季列表可以用了（issue #14：选择赛季时报错 "cannot convert 39 to Array"，因为每个赛季的
  `value` 必须是列表），并显示客户端的赛季名称（World Legend、02 Chinese Legend 等）。搜索结果也不再列出
  Liga 和 Cup 中 AI 俱乐部所用的引擎专用卡（带 '17 赛季徽章，评分最高 127，例如 L. Messi 125；
  这些卡反正也买不到）：它们所在的赛季 0 已从 `/static/roster` 中移除，其中的 "default" 条目现在是
  客户端自己的 '17 赛季（88），所以赛季列表中 "2017" 只出现一次。
- **所有可交易的球员卡**都出现在 Market、交易和卡包中：只要 fifaaddict 收录或游戏自身标记为可交易
  （`permid.transferable`），就算在内。只用 fifaaddict 会漏掉 6,599 张卡（其中 4,313 张是 '17）；
  Market 现有 44,703 张。新的 **Semua Pemain**（所有球员）卡包可以开出其中任何一张。
  原本不可交易的 FO3 活动卡也算在内（卡福、卡恩、里瓦尔多等第 26 赛季传奇，EL / CEL 传奇……）：install 会在
  客户端把所有卡标为可交易（Market 搜索只列出这类卡），Market 的赛季列表也补上缺少的赛季（26、32、38、41、57）。
  在 Market 搜索筛选中选定的俱乐部也会列出其前球员的卡（按俱乐部经历，playerhistory 表）：install 会修补
  Module.big 中的 TransferUtil.teamidQuery。
- **Tim > Klub > Informasi tim**（issue #9）显示成立日期、主场球场和"建造年份 / 容量"
  （`coach.career`、`coach.stadium`、来自客户端球场表的 `GET /stadium/<id>`），以及球员面板（切换器、
  3D 球员、价格、强化等级），没有球场数据时它一直是空的。客户端数据库中的名称按 UTF-8 读取
  （`tools/re/eadb.py` 以前按 latin-1 读取）。
- **开卡包光效**随卡的价值变化：低于 1000 万 EP 没有光效，从 1000 万、1 亿和 10 亿 EP 起分别为 1-3 级。

已知问题，尚未解决：安装了 `tools/eatrax_song.py` 写入的自定义 EA Trax 歌曲后，每场比赛结束、Cup 界面
或大厅重新加载时，游戏会耗尽 RAssets 内存池（崩溃于 `FifaRNA::Crowd::PlacementData::Seats[]`）；使用
游戏自带的音乐则不会。把歌曲做成原始 PCM（37.7 MB）、作为第 10 首加入的 EALayer3（6.5 MB，
`tools/re/ealayer3.py`）、以及替换 TrackID 1 且音乐库比原版更小的 EALayer3，都会同样崩溃，所以原因不是
音乐大小。请使用游戏自带的音乐：`python -I tools/eatrax_song.py --restore` 可以恢复。

尚未实现：与其他玩家的在线对战（排位 PvP、友谊赛、King of the Hill、俱乐部比赛）、Road to World、联赛模拟。

## 运行需求

- Windows、Python 3.12、`pip install cryptography`（messenger + 数据库提取需要）。
- 已安装的 FO3 ID 客户端（默认路径 `D:\FO3ID\32795`；装在别处时把 `FO3_GAME` 设为该文件夹；如果在那里
  找不到客户端的图片缓存，服务器启动时会给出警告）。
- 可选：ffmpeg（只有 `tools/eatrax_song.py` 需要）。

## 安装

**最简单：setup.exe。** 关闭游戏后运行 `FO3ID-Server-Setup-<版本>.exe`：它会询问 FO3 ID 游戏文件夹，连同自带的
Python 安装服务器（到 `C:\FO3ID Server`），生成证书，添加 hosts 条目，提取球员数据库，为游戏打补丁
（`installer/setup_pc.py`，日志在 `installer/setup.log`），并在桌面创建 **FO3ID** 快捷方式：双击即可开玩。
安装包里没有游戏文件，所以游戏必须已经装好；FO4 赛季、俱乐部生涯和 3D 脸型需用下面的工具获取。卸载会还原游戏的
原始文件。制作方法：`python installer/build.py`（需要 Inno Setup 6；Python 和依赖包只下载一次）。

手动安装：

1. 从你的客户端提取球员数据库：
   `python extract_fifadb.py D:\FO3ID\32795` → `gamedata/fifadb.sqlite`。
2. 可选的市场价格：`python tools/fa_sync.py` → `gamedata/fa_prices.json`（礼貌抓取 fifaaddict.com；
   没有它时价格按评分公式计算）。
3. 为客户端写死的 API 主机 `APSEODESK172` 生成 TLS 证书（接受自签名证书）：

   ```
   openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=APSEODESK172" -addext "subjectAltName=DNS:APSEODESK172" -keyout key.pem -out cert.pem
   python -c "open('certs/server.pem','w').write(open('cert.pem').read()+open('key.pem').read())"
   ```

4. hosts 文件（以管理员身份）：`127.0.0.1 APSEODESK172`。
5. 编译一次启动器（`launcher/fifalauncher.cs`，编译命令写在文件里），然后双击 **`play.bat`**：它会打开
   服务器窗口（`python start_all.py` = `http_config.py 443 --tls` + `messenger.py`；关闭该窗口即停止
   服务器），并在不弹出 UAC 提示的情况下启动游戏。
   在此之前它会运行 `tools/fo4_season.py install --if-needed`：客户端还没有本服务器的球员卡、俱乐部、3D 脸和补丁时
   （换了电脑、新客户端、上次 install 之后有改动）就会安装（一两分钟，需关闭游戏）；上次 install 记录在游戏目录的
   `fo3id_install.json`。换电脑时把 `gamedata/` 和 `backup/` 也复制过去（它们不在 git 里）。

6. 也可以手动：先运行 `python start_all.py`，再用服务器参数启动客户端（即
   `launcher/fifalauncher.exe` 和 `launch_game.ps1` 传入的参数）：

   ```
   fifazf.exe -QUICKMATCH/SERVER=127.0.0.1:28000 -QUICKMATCH/PROTOCOL=tcp -QUICKMATCH/NOTISERVER=127.0.0.1:28001 -FIFA/LAUNCHER/PID=<pid> -PUBLISHER/GARENA/TOKEN=testtoken -LOCALE/PUBLISHING_REGION=ID -DUMPTRUCK/SERVER=127.0.0.1 region id token testtoken
   ```

## 文件

| 文件 | 作用 |
|---|---|
| `http_config.py` | 客户端调用的 REST 后端（`https://APSEODESK172`）：登录、账号、经理、创建俱乐部、阵容、联赛、杯赛、市场、强化、交易、商城、物品、Bag、每日奖励、静态表，以及 `/cdn/`（从客户端自己的 `_cache` 提供队徽和球员卡）。数据保存在 `gamedata/state.json`。环境变量：`FO3_LANGUAGE`（默认 `ind`）、`FO3_GAME`（客户端文件夹）、`FO3_ASSETS`、`FO3_HALF_LENGTH`、`FO3_ADMIN_PORT`。 |
| `messenger.py` | 通知服务器，端口 `:28002`。帧格式 `<u16 LE len><u16 type=2><Blowfish-CBC(key "kueec", IV 0) of JSON+"\n">`；响应 `auth`、`ping`、`signon`。`python messenger.py --selftest`。 |
| `admin.py` + `admin/index.html` | 管理后台（由 `http_config.py` 启动，仅限本机访问，无密码）。其商城标签页保存到 `gamedata/shop.json`。页面上的所有文字（包括错误信息）都在 `admin.py` 末尾的 `I18N` 表中，每种语言一张表；新增语言只需再加一张表，`python admin.py` 会检查所有表的键是否一致。 |
| `responses/` | 各接口的 JSON 响应体（`/a/b/c` → `responses/a_b_c.json`，每次请求重新读取）。结构来自客户端的 AS3 代码。 |
| `extract_fifadb.py` | 客户端球员数据库（`data_0.big`，RefPack + Blowfish）→ SQLite。 |
| `gen_teams.py` | 根据 `gamedata/fifadb.sqlite` 生成初始阵容和俱乐部概要。 |
| `tools/eatrax_song.py` | 把一首歌以 EALayer3 格式放到 EA Trax 菜单音乐的第一首，替换 TrackID 1（以追加方式修改 `data_0.big` / `data_1.big`；`--restore` 恢复原文件）。会导致比赛后崩溃，见已知问题。 |
| `tools/fa_sync.py` | 同步 fifaaddict.com 价格。 |
| `tools/fo4_season.py` | 把 FIFA Online 4 的 26 LIVE 和 25 LIVE 球员卡（fifaaddict 数据，属性按 +0 计）加为两个新赛季，使用 FO4 的赛季徽章：`fetch`、`collect` + `import`、`build`、`install`（就地改写 `data_0.big` 中的条目，因为它的大小不能改变；以追加方式修改 `Label.big` 和 `Module.big`；`restore` 恢复这些文件）。`hud` 只修补 `Module.big`，用于之前已安装赛季的客户端：比赛中的球员名牌以前对所有 2017 和 FO4 球员卡都显示 '17 徽章，现在显示球员卡自己的赛季。`wc18` 为 `build` 读取 2018 世界杯大名单中最好的 300 名球员（futwiz 的 FIFA 18 世界杯评分，维基百科的大名单和俱乐部）：赛季 WC18（代码 78），+1 时卡面评分为世界杯评分 - 5，徽章是与 FO3 自己的世界杯徽章同款绿色的“18”；球队颜色为“2018 WC All-Star”（赛季）和“18 WC <国家>”（各国球员卡），特性取自球员的 FO3 卡和 FIFA 18 卡。 |
| `tools/fa_faces.py` | 补充客户端缓存中缺少的球员小头像（fifaaddict 提供同样的 128x128 PNG）；`--crests` 补充缺少的俱乐部队徽（大多数俱乐部在缓存中没有，游戏会显示默认队徽）；`--staffs` 补充教练照片（缓存中只有服务器发放的 71 名教练中的 7 张）。可断点续传，`--remove` 撤销。 fifaaddict 没有的球员头像从 FC Online（FO4）获取；`--all` 获取所有类型。 |
| `tools/club_history.py` | 球员信息特性页截至当前赛季的俱乐部经历：按出生日期和姓名把 Wikidata 的足球运动员与 FO3 球员对应，其俱乐部（P54：年份、租借；不含国家队和青年队）对应到 EA 球队 ID（FO3 自带经历，其次 TEAMNAMES 名称，否则使用写入 TEAMNAMES 的新 ID），保存到 `gamedata/club_history.json`；`fo4_season.py install` 把它放进客户端。`show 姓名` 显示一名球员的经历。 |
| `tools/photo_head.py` | 用照片制作 3D 脸：读写客户端的扫描头文件（RX3 / chunkzip，512×512 DXT1 脸部贴图），按 11 个点把照片变形到脸部贴图上并融合（OpenCV），再用照片头的文件（供体的网格和头发，新头 ID 从 99,000,000 起）重新生成 `data_7.big`。`uv HEADASSETID OUT.png` 保存某个头的脸部贴图。 点可来自 YuNet（`landmarks`），供体来自 `donor_looks` / `suggest`。 |
| `tools/draft_icons.py` | 绘制选秀卡包的商店图标（客户端缓存里没有选秀图片），保存到 `assets/externalAssets/shop/`，服务器优先提供这些图片；`<icon> <tag> <picks>` 可再画一个，用于管理页面创建的卡包。需要 Pillow。 |
| `tools/re/` | 逆向工程辅助工具：`bigdir.py`（BIg4 归档表）、`refpack.py`（RefPack 编解码）、`eadb.py`（EA "DB" 数据库表）、`abc*.py`（AS3 字节码解析 / 搜索 / 结构挖掘）、`gfxx.py`（界面 SWF）、`memgrab.py`、`dbwin.py`（客户端调试输出）、`relay_emu.py`（PvP 中继数据包编解码和测试中继）、`gamectl.ps1`（测试时操控客户端窗口）。 |
| `blaze.py`、`server.py`、`test_client.py`、`analyze_capture.py` | 项目第一阶段的 EA Blaze 帧格式（Fire/TDF）和回环测试服务器；后来发现客户端用的是 REST + messenger。 |
| `analysis/` | 早期的客户端分析脚本（字符串、PE 节、网络接口）。 |
| `FO3ID-private-server.md`、`reports/` | 研究发现和进度报告（`reports/` 为印尼语）。 |

## 测试

```
python run_tests.py
```

运行 Blaze/回环自测和 `test_league.py`；后者用一个新账号走一遍所有后端功能（创建俱乐部、联赛、杯赛、
市场、强化、交易、商城、每日奖励、管理 API、删除经理）。需要 `gamedata/fifadb.sqlite`。

## 贡献者

- **SoloPlayer** — 项目所有者，在真实客户端中测试。
- **Claude**（Anthropic 的 AI 助手，通过 Claude Code）— 逆向工程和大部分服务器代码，与项目所有者共同编写。

## 许可证

本仓库中的代码和文档使用 [GNU GPL v3](LICENSE)。FIFA Online 3 及其客户端、数据、素材和音乐归各自的
所有者所有，不在此许可范围内。
