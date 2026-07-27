# Xatu 校准的 Beacon 拓扑模型

本文将定义和拟合结果严格分开。第一至第三部分只说明观测模型、变量、估计方法、
图生成器和评价指标，不使用最终参数值反过来证明定义合理；第四部分再集中报告
连续十天 Xatu 数据在这些假设下得到的结果。

## 第一部分：观测与定义

### 1. Xatu 到底观测到了什么

#### 1.1 观测边界与一分钟快照

参数估计使用 2026-07-07 至 2026-07-16 连续十天的 Xatu Mainnet 数据。时间切分
依次为五天训练、两天特征验证和三天最终测试。Observer 身份只能由拟合日期选择；
最终 train-plus-validation refit 保留 26 个 observer，它们至少在 80% 的拟合日期
属于 bounded，且从未在拟合日期被分类为 bulk probe。

Xatu 直接观测到的边必然有一个 observer 端点。若 observer $O$ 看到了节点 $A$
和 $B$，数据只能确定 $O-A$ 和 $O-B$，无法判断 $A-B$ 是否存在。隐藏图中存在
$A-B$ 与不存在 $A-B$ 都会产生同样的 Xatu 记录，因此 remote-to-remote 邻接关系
仅凭这类数据不可识别。

所以，本模型不声称恢复 Mainnet 真实邻接矩阵，也不从 observer degree 推断普通
remote peer 的 degree。Observer 的连接数部分由测量软件和探测策略决定。模型只
学习 bounded observer 边上可测的出现和持续规律，再把这些规律对称地用于生成
remote-to-remote 合成图。

令：

- $o$ 表示一个 bounded observer；
- $i$ 表示一个 remote peer；
- $t$ 表示一个一分钟时间桶；
- $B$ 表示某个拟合日期实际存在的 bounded observer 数量；
- $T$ 表示数据中的一分钟时间桶总数；
- $\Delta=10$ 分钟表示一次拓扑变化的观察间隔。

定义 observer 与 remote peer 的观测边：

$$
e_{oit}=
\begin{cases}
1,&\text{observer }o\text{ 在时间桶 }t\text{ 与 peer }i\text{ 相连},\\
0,&\text{否则}.
\end{cases}
$$

这个二值变量是后续所有统计量的基础。Xatu 直接给出的是
observer-to-remote 边，因此模型先学习节点在这些边上的形成和持续行为，再将
学习到的行为对称地应用到 remote-to-remote 节点对。

比较相隔 $\Delta$ 的两次观测，可以得到四种区间级边事件：

| $t$ 时刻 | $t+\Delta$ 时刻 | 观测事件 |
|---:|---:|---|
| 0 | 0 | 持续未被观察到 |
| 0 | 1 | formation/出现 |
| 1 | 1 | retention/保留 |
| 1 | 0 | dissolution/断开 |

$0\rightarrow1$ 只说明一条观测边在区间内出现，不能给出精确建连时刻。这里的 0
也只能解释为“该 observer 没有观察到”，不能直接解释为节点离线。

一分钟是离散化精度，十分钟是选择的 churn 观察尺度，两者都不是 Ethereum 协议
常数。十分钟是在“短间隔几乎不发生变化”和“长间隔会掩盖多次连接/断开”之间的
折中。稳健性分析仍应在 5、20、30 分钟等尺度上重新拟合并比较结果。

节点 $i$ 在拟合日期 $d$ 的可观测时间比例为：

$$
a_{id}=
\frac{1}{T_d}
\sum_{t\in d} I\left(\sum_o e_{oidt}>0\right).
$$

只要任意 bounded observer 在时间桶 $t$ 看到了节点 $i$，该时间桶就计为 1。
因此 $a_i$ 衡量节点在一天内被观测到的持续程度。它是在线程度的代理量，而不是
节点真实 uptime，因为节点在线但没有连接任何 observer 时不会被看到。

节点 $i$ 在日期 $d$ 的 observer 覆盖比例为：

$$
v_{id}=
\frac{1}{B_d}
\sum_o I\left(\sum_{t\in d} e_{oidt}>0\right).
$$

$v_{id}$ 衡量该日有多少个不同 observer 曾看到节点。实现中先计算每天的
$a_{id}$、$v_{id}$，再按每天的 exposure 加权后交给 Ridge 模型。

#### 1.2 事件数量与 Exposure

Exposure 表示“一个事件有机会被观察到一次”，也就是统计比例的分母。只统计
事件数量而不统计 exposure 会产生偏差。例如，节点 A 被观察 1,000 次并保留
800 次，节点 B 被观察 2 次并保留 2 次。B 的表面保留率是 100%，但证据远少于
A；exposure 使模型能够区分这两种情况。

本模型使用三种 exposure。

**Formation opportunity**

节点 $i$ 的形成机会先在每个拟合日内近似，再跨日相加：

$$
O_i=
\sum_{d\in\mathcal D_i}
\max(1,a_{id}T_dB_d).
$$

$a_{id}T_d$ 是节点在日期 $d$ 被观察为可用的一分钟时间桶数，再乘该日 observer
数 $B_d$ 得到 observer-bucket 机会。模型不会跨午夜连接两个日期的 transition，
所以一天末尾不会被当成下一天开头的前一时刻。取 $\max(1,\cdot)$ 避免分母为零。

例如，某节点在 100 个一分钟桶中被观察到，bounded observer 有 26 个，那么该
近似提供 $100\times26=2{,}600$ 个 observer-bucket 机会。这并不表示 26 个
observer 当时都知道它的地址，而是用“至少被一个 observer 看到”近似它对测量系统
可用。

节点 $i$ 的新边事件数为：

$$
F_i=
\sum_{o,t\ge\Delta}
e_{oit}(1-e_{oi,t-\Delta}).
$$

如果边 $(o,i)$ 在 $t$ 存在、在十分钟前不存在，就计为一次形成事件。因此
$F_i/O_i$ 表示每个可用机会产生新连接的频率。

理想的 formation 分母应包含每个 observer 当时知道的、在线的、尚未连接的全部
候选节点。但 Xatu 不记录完整候选集、失败拨号和从未连接 observer 的在线节点。
因此 $O_i$ 及 $F_i/O_i$ 都是近似量，后者只能作为节点之间的相对 formation
倾向，不能解释为普通 Mainnet 节点的绝对拨号概率。

**Retention exposure**

边在 $t$ 已经存在，而且数据中还有 $t+\Delta$ 的观测时，才能检验它十分钟后
是否仍存在。节点 $i$ 的 retention exposure 为：

$$
E_i=
\sum_{o,t:\,t+\Delta\le T}e_{oit}.
$$

每个满足条件的现有边都是一次 retention exposure。十分钟后仍存在的次数为：

$$
R_i=
\sum_{o,t:\,t+\Delta\le T}
e_{oit}e_{oi,t+\Delta}.
$$

因此 $R_i/E_i$ 是节点 $i$ 的十分钟边保留率。例如，某条边分别在 12:00、
12:01 和 12:02 存在，这三个时刻都可以各自接受一次十分钟保留检验，所以会形成
三个 exposure，而不是只算一个 session。

例如，12:00 有三条边，12:10 仍存在两条，则 $E=3$、$R=2$，保留率为 $2/3$。
Retention 的风险集合可以直接观察：每条当前存在的边都能检查未来状态。因此它比
formation 更容易识别，也更可靠。

每分钟滑动一次会让相邻检验重复使用同一 session，所以这些样本不是相互独立的。
它们可以用于估计保留率点值，但置信区间不能直接套用独立二项分布；应使用按 observer
或时间块的 bootstrap，或者用不重叠时间窗做稳健性检验。

#### 1.3 两种不同的 Formation 量

模型把“新边分配给谁”和“总共形成多少新边”分开。节点级统计量

$$
\frac{F_i}{O_i}
$$

比较哪些节点相对更容易获得观测边，并进入节点效应 $q_i$。拓扑变化使用的全局量

$$
\rho_C=\frac{\sum_iF_i}{\sum_iE_i}
$$

表示每拥有一条当前边，一轮变化应形成多少条新边。它控制生成图的 formation 总量。
用当前边 exposure 归一化，可以让新增边数随图规模变化，同时不假装 Xatu 已经给出
完整 remote-to-remote 非边候选集。

## 第二部分：统计模型与图生成模型

### 2. 经验贝叶斯节点倾向

原始比例 $F_i/O_i$ 和 $R_i/E_i$ 在 exposure 很少时非常不稳定。一个只接受两次
检验且两次成功的节点，不应比接受 1,000 次检验并成功 800 次的节点提供更强证据。
因此，模型把低 exposure 节点收缩到全局平均，同时允许观测充分的节点保留自身行为。

#### 2.1 Formation 平滑与形成效应

全局每机会形成率为：

$$
\rho_F=
\frac{\sum_iF_i}{\sum_iO_i}.
$$

模型为潜在 formation 概率设置均值为 $\rho_F$、强度为 20 的 Beta 先验：

$$
p_i^F\sim
\operatorname{Beta}
\left(20\rho_F,20(1-\rho_F)\right).
$$

在 $O_i$ 个近似机会中观察到 $F_i$ 次 formation 后，后验均值为：

$$
\widehat\rho_{F,i}
=
\frac{F_i+20\rho_F}{O_i+20}.
$$

它也可以写成：

$$
\widehat\rho_{F,i}
=
\frac{O_i}{O_i+20}\frac{F_i}{O_i}
+
\frac{20}{O_i+20}\rho_F.
$$

这个形式直接说明了为什么这样估计：它是节点原始比例与全局比例的加权平均。20
表示 20 个全局平均水平的伪机会，是控制方差的超参数，不是 Ethereum 常数。

节点的原始形成效应定义为平滑 formation rate 相对全局 rate 的对数倍数：

$$
q_i^{raw}
=
\operatorname{clip}
\left(
\log\frac{\widehat\rho_{F,i}}{\rho_F},
-6,6
\right).
$$

$q_i^{raw}=0$ 表示节点等于全局平均；$q_i^{raw}=\log2$ 表示约为全局的两倍；
$q_i^{raw}=-\log2$ 表示约为一半。Formation 是低概率计数事件，Poisson 强度模型
自然使用 log link；概率较小时 $\operatorname{logit}(p)\approx\log p$，所以该值
也可以近似作为后续 Logistic 模型的加性效应。裁剪用于阻止稀疏观测产生极端分数。

#### 2.2 Retention 平滑与持续效应

全局十分钟保留率为：

$$
\rho_R=
\frac{\sum_iR_i}{\sum_iE_i}.
$$

Retention 先验的均值为 $\rho_R$、强度为 50：

$$
p_i^R\sim
\operatorname{Beta}
\left(50\rho_R,50(1-\rho_R)\right).
$$

对应后验均值为：

$$
\widehat\rho_{R,i}
=
\frac{R_i+50\rho_R}{E_i+50}
=
\frac{E_i}{E_i+50}\frac{R_i}{E_i}
+
\frac{50}{E_i+50}\rho_R.
$$

因此，检验很少的节点接近全局平均，检验很多的节点主要由自身数据决定。20 和 50
仍应通过 held-out 或敏感性分析验证；当前值是正则化选择，不是理论上唯一的常数。

原始持续效应使用 log-odds 差：

$$
r_i^{raw}
=
\operatorname{clip}
\left[
\operatorname{logit}(\widehat\rho_{R,i})
-\operatorname{logit}(\rho_R),
-6,6
\right],
$$

其中：

$$
\operatorname{logit}(p)=\log\frac{p}{1-p}.
$$

Logistic 模型满足 $\operatorname{logit}(P)=\text{线性分数}$，因此该差值可以直接
作为 retention 模型中的加性效应。正值表示比全局平均更稳定，负值表示更容易断开。

#### 2.3 Session exposure

Heartbeat session 是同一个 observer-peer 连接连续出现的一段时间。对节点
$i$，令：

- $N_i$ 为可用 session 数；
- $L_{is}$ 为第 $s$ 个 session 已观察到的时长；
- $T_i^{session}$ 为所有 session 的总观察时长；
- $D_i$ 为观察到明确结束的 session 数；
- $M_i$ 为 session 观察时长的中位数。

总 session exposure 为：

$$
T_i^{session}
=
\sum_{s=1}^{N_i}\max(L_{is},1\text{ minute}).
$$

一分钟下限与数据的时间桶精度一致，避免零时长 session 不贡献 exposure。
Session exposure 的单位是时间，它回答“连接总共处于风险状态多久”；retention
exposure 的单位是检验次数，它回答“进行了多少次十分钟后仍存在的检验”。

#### 2.4 删失 session 与寿命估计

如果 session 在数据开始前已经建立，则开始时间未知，称为左删失。模型排除这类
session，因为无法计算已观察到的完整起点。

如果 session 在数据结束时仍然存在，则结束时间未知，称为右删失。它已经存活的
时间仍然是有效信息，所以计入 $T_i^{session}$；但不能假定它此时结束，因此不
增加 $D_i$。

在连接结束风险近似恒定的指数寿命模型中，结束率 $\lambda$ 的似然只取决于结束
事件数和总 exposure：

$$
L(\lambda)\propto
\lambda^{\sum_iD_i}
\exp\left(-\lambda\sum_iT_i^{session}\right).
$$

对数似然求导并令其为零：

$$
\frac{\partial\log L}{\partial\lambda}
=
\frac{\sum_iD_i}{\lambda}
-\sum_iT_i^{session}
=0,
$$

得到最大似然结束率和平均寿命：

$$
\widehat\lambda
=
\frac{\sum_iD_i}{\sum_iT_i^{session}},
\qquad
M_{global}
=
\frac{1}{\widehat\lambda}
=
\frac{\sum_iT_i^{session}}{\sum_iD_i}.
$$

这说明为什么右删失 session 应贡献已观察时长而不能贡献一次结束。假设一条连接
在数据结束时已存活 8 小时，它提供“寿命至少 8 小时”的信息，而不是“寿命等于
8 小时”。

单个节点可能只出现很少的 session。直接使用
$T_i^{session}/D_i$ 会在 $D_i=0$ 时无穷大，也会在 $D_i$ 很小时剧烈波动。
因此加入相当于 10 个全局平均 session 的经验贝叶斯伪数据：

$$
\widetilde M_i=
\frac{T_i^{session}+10M_{global}}{D_i+10}.
$$

分子增加 $10M_{global}$，分母增加 10，等价于先验中已有 10 次结束、每次平均
寿命为 $M_{global}$。当 $D_i$ 很大时，真实数据主导；当 $D_i$ 很小时，估计值
收缩到全局均值。常数 10 是正则化强度，不是协议常数。

#### 2.5 Ridge 部分池化

仅用 $q_i^{raw}$ 和 $r_i^{raw}$ 时，低 exposure 节点仍然不稳定。模型进一步
利用节点属性预测一个更平滑的节点效应。连续特征为：

$$
u_i=
\left[
\operatorname{logit}(a_i),
\operatorname{logit}(v_i),
\log(1+N_i),
\log\left(1+\frac{M_i}{1\text{ minute}}\right),
\log\left(1+\frac{\widetilde M_i}{1\text{ minute}}\right)
\right].
$$

对 $a_i$ 和 $v_i$ 使用 logit，是因为它们位于 $(0,1)$，logit 将其映射到整条
实数轴；对 session 数和时长使用 $\log(1+x)$，是为了压缩长尾，避免少数超长
session 支配回归。五个连续特征分别减均值、除以标准差：

$$
z_{ik}=
\frac{u_{ik}-\overline u_k}{s_k}.
$$

标准化让 Ridge 对各连续特征施加可比较的惩罚。最终设计向量为：

$$
X_i=
\left[
1,\,
z_i,\,
\text{client one-hot},\,
\text{country one-hot},\,
\text{ASN one-hot},\,
\text{organization one-hot}
\right].
$$

one-hot 只保留最高频的 8 个客户端、10 个国家、16 个 ASN 和 12 个组织，避免
为只出现一两次的类别拟合不可靠系数。这里的国家、ASN 和组织是节点级协变量，
用于预测 $q_i$ 与 $r_i$，不是节点对之间是否连边的额外变量。

令：

$$
P=\operatorname{diag}(0,1,\ldots,1).
$$

首位 0 表示不惩罚截距，其余系数受到 Ridge 惩罚。两个回归目标为：

$$
\widehat\theta_q
=
\arg\min_{\theta_q}
\left[
\sum_i
\left(q_i^{raw}-X_i^T\theta_q\right)^2
+10\theta_q^TP\theta_q
\right],
$$

$$
\widehat\theta_r
=
\arg\min_{\theta_r}
\left[
\sum_i
\left(r_i^{raw}-X_i^T\theta_r\right)^2
+10\theta_r^TP\theta_r
\right].
$$

对目标函数求导并令其为零，可以得到 Ridge 闭式解：

$$
\widehat\theta
=
\left(X^TX+10P\right)^{-1}X^Ty.
$$

$10\theta^TP\theta$ 对大系数施加二次代价。从贝叶斯角度，它等价于给非截距系数
加入均值为 0 的高斯先验；从数值角度，它缓解 one-hot 特征共线和稀疏类别导致的
过拟合。Ridge 预测为：

$$
q_i^{reg}=X_i^T\widehat\theta_q,
\qquad
r_i^{reg}=X_i^T\widehat\theta_r.
$$

直接估计与 Ridge 预测按观测可靠性组合：

$$
w_i^q=\frac{F_i}{F_i+12},
\qquad
q_i^*
=
w_i^qq_i^{raw}
+(1-w_i^q)q_i^{reg},
$$

$$
w_i^r=\frac{E_i}{E_i+120},
\qquad
r_i^*
=
w_i^rr_i^{raw}
+(1-w_i^r)r_i^{reg}.
$$

当 $F_i=0$ 时，$w_i^q=0$，形成效应完全采用回归预测；当 $F_i=12$ 时，两部分
各占一半；当 $F_i$ 很大时，节点自身数据占主导。Retention 的 120 具有同样含义。
这属于经验贝叶斯部分池化：证据少的节点向群体模型收缩，证据多的节点保留个体
差异。

最后使用中位数中心化和标准差缩放：

$$
q_i=
\operatorname{clip}
\left(
\frac{q_i^*-\operatorname{median}(q^*)}
{\operatorname{sd}(q^*)},
-3,3
\right),
$$

$$
r_i=
\operatorname{clip}
\left(
\frac{r_i^*-\operatorname{median}(r^*)}
{\operatorname{sd}(r^*)},
-3,3
\right).
$$

中位数比均值更不容易被极端节点拖动；标准化让 $q_i$、$r_i$ 处于可比较尺度；
裁剪限制单个节点对边概率的支配程度。这里改变的是分数尺度，不改变大多数节点的
相对排序。

### 3. Same-ASN 节点对效应

#### 3.1 候选节点对变量与 Logistic 概率

节点对变量筛选考虑 same ASN、organization、country 和 city。以下公式中的
$S_{ij}$ 表示经过时间切分筛选后保留的二值变量，第四部分再报告最终保留了哪一个。
以 same ASN 为例，其定义为：

$$
S_{ij}=I(ASN_i=ASN_j).
$$

两个节点 ASN 相同且 ASN 信息非空时 $S_{ij}=1$，否则为 0。概率通过 sigmoid
函数从线性分数得到：

$$
\sigma(z)=\frac{1}{1+\exp(-z)}.
$$

其关键性质是：

$$
\frac{\sigma(z)}{1-\sigma(z)}=\exp(z).
$$

因此在线性分数中增加系数 $\beta$，等价于把事件 odds 乘以 $\exp(\beta)$。
这使系数具有直接的概率解释。

#### 3.2 Formation 系数

Formation 每天最多抽取 10,000 个匹配排序样本。每个真实新边样本由 observer $o$、
真实新连接节点 $i$ 和对照节点 $j$ 组成。$j$ 在同一时间桶可用，但没有连接到
$o$。模型比较 $i$ 相对于 $j$ 被选中的概率：

$$
P(i\succ j)
=
\sigma
\left[
(q_i-q_j)
+\beta(S_{oi}-S_{oj})
\right].
$$

这是条件 Logistic 回归，也可看作 Bradley-Terry 两两排序模型。假设节点的选择
分数为 $q_i+\beta S_{oi}$，比较 $i$ 和 $j$ 时，observer 和时间桶共有的基准项
相消，剩下两个分数之差。因此该模型不需要为每个 observer、每个时间桶单独估计
截距。

Formation 模型没有截距，使用 $L_2$ 强度 2 的惩罚 Logistic 最大似然。惩罚后的
目标等价于：

$$
\widehat\beta
=
\arg\min_\beta
\left[
-\sum_m\log P(i_m\succ j_m)
+\frac{\lambda}{2}\beta^2
\right].
$$

第一项要求真实新连接比对照节点得到更高概率，第二项防止有限样本将系数推得过大。
当前实现取 $\lambda=2$，所以第二项的数值为 $\beta^2$，其梯度为
$2\beta$，与拟合器中的正则化梯度一致。

#### 3.3 Retention 系数

对每条在 $t$ 已存在的 observer-peer 边定义结果：

$$
y_{oit}=
\begin{cases}
1,&e_{oi,t+\Delta}=1,\\
0,&e_{oi,t+\Delta}=0.
\end{cases}
$$

十分钟保留概率为：

$$
P(y_{oit}=1)
=
\sigma(c+r_i+\gamma S_{oi}).
$$

$c$ 表示全局基准保留 log-odds，$r_i$ 表示节点自身持续倾向，$\gamma$ 表示在
控制 $r_i$ 后 same-ASN 额外带来的 log-odds 变化。该模型每天最多抽取 10,000 个
active-edge exposure。令
$p_m=\sigma(c+r_{i_m}+\gamma S_{o_mi_m})$，其目标为：

$$
(\widehat c,\widehat\gamma)
=
\arg\min_{c,\gamma}
\left\{
-\sum_m
\left[
y_m\log p_m
+(1-y_m)\log(1-p_m)
\right]
+\frac{\lambda}{2}\gamma^2
\right\},
\qquad
\lambda=2.
$$

这是 Bernoulli 负对数似然加 Ridge 惩罚。截距 $c$ 不受惩罚，因为它需要自由地
拟合全局保留水平；$\gamma$ 受惩罚，以限制 same-ASN 效应的方差。

$\beta$、$\gamma$ 的拟合值、odds 乘数和 held-out 证据统一放在第四部分。这样可以
避免把数据中拟合出的相关关系误读成模型预先规定的规则。

节点对变量在五个完整训练日上拟合，并要求训练期前后两个日期组中的系数符号一致；
变量还必须在随后两个验证日上至少降低 $5\times10^{-4}$ log loss。选定特征后，
模型在全部七个拟合日上重新拟合。最后三个测试日只评分一次，不参与节点效应、
observer 选择、Ridge 系数、节点对特征选择或节点对系数拟合。

将 observer-to-remote 系数应用到 remote-to-remote 节点对依赖一个条件可交换
假设：在给定节点倾向和 same-ASN 关系后，observer 身份不再改变形成与保留效应
的方向。该假设允许把 Xatu 能测到的边行为转移到待生成的对称 P2P 图。

### 4. 初始图生成

#### 4.1 图变量

令 $n$ 为配置的生成节点数。初始节点按固定随机种子从所选初始时间桶中被 bounded
observer 看到的候选节点里抽取，因此每个生成节点在初始时刻都有可用证据。第四部分
统一报告当前的 $n$、随机种子、时间桶和候选节点数量。

定义无向图变量：

$$
x_{ij}^{(t)}=
\begin{cases}
1,&\text{快照 }t\text{ 包含边 }(i,j),\\
0,&\text{否则}.
\end{cases}
$$

节点 degree 为：

$$
d_i^{(t)}
=
\sum_{j\ne i}x_{ij}^{(t)}.
$$

根据握手定理，每条无向边为两个端点各贡献 1 degree，所以：

$$
\overline d_t
=
\frac{1}{n}\sum_i d_i^{(t)}
=
\frac{2|E_t|}{n}.
$$

#### 4.2 对称节点对分数

Formation 分数为：

$$
f_{ij}=q_i+q_j+\beta S_{ij}.
$$

$q_i+q_j$ 是从 observer-target 排序模型到两个普通 remote peer 的对称扩展：
两个端点形成连接的倾向都参与边的形成。$\beta S_{ij}$ 加入实测的 same-ASN
形成修正。

Retention 分数为：

$$
h_{ij}
=
\frac{r_i+r_j}{2}
+\gamma S_{ij}.
$$

训练 retention 模型时每个样本只有一个 remote endpoint 的 $r_i$；生成
remote-to-remote 边时必须同时考虑两个端点。使用平均值而不是求和，可以对称地
包含双方，同时保持与训练时单个 $r_i$ 相近的数值尺度。

初始边综合分数为：

$$
z_{ij}^{(0)}
=
f_{ij}+h_{ij}
=
q_i+q_j
+\frac{r_i+r_j}{2}
+(\beta+\gamma)S_{ij}.
$$

初始快照没有可直接观测的 remote-to-remote 边标签，因此不能用监督学习直接
拟合 $x_{ij}^{(0)}$。这里采用组合评分：初始边既应容易形成，也应具有持续性。
该加法来自 Logistic 模型中 log-odds 分数可加的性质，是一个同时偏好 formation
和 retention 的生成准则；它不是从隐藏 Mainnet 邻接矩阵拟合出的边分类器。

初始边概率为：

$$
p_{ij}^{(0)}
=
\sigma(c_0+z_{ij}^{(0)}).
$$

$c_0$ 只控制整体图密度，不改变节点对分数的排序。模型允许平均 degree 位于：

$$
6\le\overline d\le24.
$$

初始校准目标取区间中点：

$$
d^*=\frac{6+24}{2}=15.
$$

所有节点对平均边概率记为：

$$
\overline p(c_0)
=
\frac{1}{\binom n2}
\sum_{i<j}\sigma(c_0+z_{ij}^{(0)}).
$$

条件独立 Bernoulli 图的期望平均 degree 为：

$$
\mathbb E[\overline d]
=
\frac{2}{n}\sum_{i<j}p_{ij}^{(0)}
=(n-1)\overline p(c_0).
$$

令 $\mathbb E[\overline d]=d^*$，得到截距校准方程：

$$
\overline p(c_0)=\frac{d^*}{n-1}.
$$

$\overline p(c_0)$ 随 $c_0$ 单调增加，因此可以用二分搜索唯一求解。随后每个节点
对独立采样：

$$
x_{ij}^{(0)}
\sim
\operatorname{Bernoulli}(p_{ij}^{(0)}).
$$

独立 Bernoulli 是带节点和节点对协变量的随机图基础形式。采样后施加连通性和
peer cap 约束，因此最终边并非完全独立。生成器最多尝试 20 个随机样本，接受平均
degree 位于宽松区间的样本，而不为每个节点指定目标 degree。

### 5. 十分钟拓扑变化

#### 5.1 现有边保留

对当前边 $(i,j)\in E_t$，保留概率为：

$$
p_{ij}^{keep}(t)
=
\sigma
\left[
c_t^{keep}
+\frac{r_i+r_j}{2}
+\gamma S_{ij}
\right].
$$

节点持续倾向和 same-ASN 系数决定不同边之间的相对稳定性，动态截距
$c_t^{keep}$ 负责使当前所有边的平均保留概率等于 Xatu 全局保留率：

$$
\frac{1}{|E_t|}
\sum_{(i,j)\in E_t}
p_{ij}^{keep}(t)
=
\rho_R.
$$

由于 sigmoid 对截距单调，$c_t^{keep}$ 同样通过二分搜索求解。每个现有边随后
进行一次 Bernoulli 保留试验。该校准保留 $r_i$ 和 same-ASN 带来的相对差异，
同时使生成图匹配测得的 $\rho_R$。

#### 5.2 非边形成

动态图使用的形成比例与节点效应中的每机会形成率不同。这里定义：

$$
\rho_C=
\frac{\sum_iF_i}{\sum_iE_i}.
$$

分母使用当前边 exposure，所以 $\rho_C$ 表示每拥有一条当前边，十分钟变化中
应形成多少条新边。

若当前有 $|E_t|$ 条边，目标新增边数为：

$$
A_t^*=\rho_C|E_t|.
$$

令 $U_t$ 为当前全部非边集合。每个非边的形成概率为：

$$
p_{ij}^{form}(t)
=
\sigma(c_t^{form}+q_i+q_j+\beta S_{ij}),
\qquad
(i,j)\in U_t.
$$

截距满足：

$$
\frac{1}{|U_t|}
\sum_{(i,j)\in U_t}
p_{ij}^{form}(t)
=
\min
\left(
0.95,
\frac{A_t^*}{|U_t|}
\right).
$$

右侧是“期望新增边数除以可选非边数”。0.95 上限避免在极端小图中要求几乎确定
形成所有非边，并保证 Logistic 截距保持有限。由期望的线性性：

$$
\mathbb E[|E_{t+1}\setminus E_t|]
=
\sum_{(i,j)\in U_t}p_{ij}^{form}(t)
\approx A_t^*.
$$

#### 5.3 边数稳定性的来源

忽略 peer cap 和连通性修复时，下一快照期望边数约为：

$$
\mathbb E[|E_{t+1}|]
\approx
\rho_R|E_t|
+\rho_C|E_t|
=
(\rho_R+\rho_C)|E_t|.
$$

当拟合出的 $\rho_R+\rho_C$ 接近 1 时，模型会替换大量具体邻居，但期望总边数
基本稳定。它是否成立应在第四部分由数据检验，而不是预先固定边数。Bernoulli 采样
下实际边数仍会波动。

模型生成五次变化，每次代表十分钟。现有边保留与非边形成分别采样，因此一条旧边
可能消失，另一条新边可能加入。

### 6. 结构约束

每个快照必须满足无向简单图条件：

$$
x_{ij}^{(t)}=x_{ji}^{(t)},
\qquad
x_{ii}^{(t)}=0.
$$

第一式表示边没有方向，第二式排除自环；边使用集合存储，因此不会出现重复边。

每个节点的安全 peer 上限为：

$$
d_i^{(t)}
\le
80-16
=64.
$$

80 是节点 peer limit，预留 16 个位置供协议运行时连接、bootstrap 或短暂 churn
使用，避免生成拓扑在启动前就耗尽连接容量。64 是安全上限，不是目标 degree。

平均 degree 只需满足：

$$
6\le\overline d_t\le24.
$$

Xatu observer 的 degree 受测量配置影响，不能用于推断普通 remote peer 的精确
degree，因此模型不为每个节点拟合或指定 degree，只使用足以运行且远低于软件
上限的宽松密度范围。

图还必须只有一个连通分量，因此不会存在孤立节点。若随机采样产生多个分量，
生成器反复加入分量之间生成概率最高、且不会违反 peer cap 的边。若某节点超过
64，则从它概率最低的边开始尝试删除，并且只接受删除后仍保持连通的操作。这些
修复将运行约束置于随机独立性之上。

## 第三部分：评价指标定义

### 7. 模型评估

#### 7.1 Churn 误差

生成图的平均十分钟保留率为：

$$
\widehat\rho_R^{gen}
=
\frac{1}{5}
\sum_{t=1}^{5}
\frac{|E_{t-1}\cap E_t|}{|E_{t-1}|}.
$$

生成图的平均十分钟形成比例为：

$$
\widehat\rho_C^{gen}
=
\frac{1}{5}
\sum_{t=1}^{5}
\frac{|E_t\setminus E_{t-1}|}{|E_{t-1}|}.
$$

对应绝对误差为：

$$
\epsilon_R=
\left|\widehat\rho_R^{gen}-\rho_R\right|,
\qquad
\epsilon_C=
\left|\widehat\rho_C^{gen}-\rho_C\right|.
$$

绝对误差直接衡量生成图的 churn 比例与 Xatu 校准目标相差多少。

#### 7.2 Held-out log loss

对真实二值结果 $y_m\in\{0,1\}$ 和预测概率 $p_m$，binary log loss 为：

$$
\operatorname{LogLoss}
=
-\frac{1}{M}
\sum_{m=1}^{M}
\left[
y_m\log p_m
+(1-y_m)\log(1-p_m)
\right].
$$

Log loss 是 Bernoulli 模型的平均负对数似然，也是严格 proper scoring rule：
只有报告真实条件概率才能最小化期望损失。它不只检查分类对错，还会强烈惩罚
“非常自信但预测错误”的样本。模型值低于不使用 same-ASN 的 baseline，说明
same-ASN 提供了额外的样本外概率信息。

Accuracy 使用 $p_m\ge0.5$ 作为正类：

$$
\operatorname{Accuracy}
=
\frac{1}{M}
\sum_m
I\left(I(p_m\ge0.5)=y_m\right).
$$

Accuracy 易于理解，但受类别不平衡影响，因此只作为辅助指标，模型选择主要使用
log loss。

Brier score 定义为 $M^{-1}\sum_m(p_m-y_m)^2$，衡量概率的平方误差，越低越好。
AUC 表示随机抽取一条保留边和一条断开边时，模型把保留边排在更前面的概率。
AUC 衡量排序，而 log loss 与 Brier 更关注概率校准，所以后二者改善时 AUC 可能
基本不变。

#### 7.3 节点属性分布

从初始候选集合抽取 $n$ 个节点后，使用 total variation distance 检查抽样是否
明显改变客户端和国家分布：

$$
\operatorname{TV}(P,Q)
=
\frac{1}{2}
\sum_k|P_k-Q_k|.
$$

$P_k$ 是候选集合中类别 $k$ 的比例，$Q_k$ 是生成节点集合中的比例。TV 为 0
表示两个分布完全一致，TV 为 1 表示没有重叠。该指标评估节点抽样代表性，不评估
边预测准确率。

## 第四部分：拟合与生成结果

### 8.1 时间切分与 Churn 参数

最终模型使用 2026-07-07 至 2026-07-16 的 Xatu Mainnet 数据。7--11 日用于训练，
12--13 日用于验证，14--16 日是独立测试集。测试事件和测试期 observer 行为均不
参与选择或重新拟合。最终七天拟合集包含 17,667 个 remote peers 和 26 个稳定的
bounded observers。测试 pair 中，formation 的端点有 96.90%、retention 的端点
有 95.99% 曾在拟合期出现；未见节点使用全局零效应先验。

最终 train-plus-validation 参数为：

$$
\rho_F=0.010299,
\qquad
\rho_R=0.766464,
\qquad
\rho_C=0.233628.
$$

因此 $\rho_R+\rho_C=1.000092$，无需固定边数，期望边数也基本稳定。三个测试日
独立测得 $\rho_R=0.776148$、$\rho_C=0.223733$。在三个测试日期上，扩展窗口的
单日提前预测误差分别位于 0.0035--0.0116（retention）和
0.0034--0.0117（formation）。

### 8.2 节点对效应拟合结果

Formation 没有任何节点对特征通过验证。单日原型曾得到负的 same-ASN formation
系数，但它不能降低两天验证集的 log loss，因此最终设为零。这说明旧的 formation
结论不具有跨日稳健性。

Retention 只保留 same ASN。七天 refit 后：

$$
\gamma=0.4752,
\qquad
\exp(\gamma)=1.608.
$$

控制节点自身 persistence 后，same-ASN 观测边的十分钟 retention odds 约为
different-ASN 边的 1.61 倍。Country、city 和 organization 在训练期的符号也
稳定为正，但在 same ASN 已存在时，不能进一步达到验证集改进阈值。

### 8.3 独立测试与生成图结果

| 指标 | Baseline 或测试观测 | 最终模型 |
|---|---:|---:|
| Formation test log loss | 0.45626 | 0.45626 |
| Formation test accuracy | 76.23% | 76.23% |
| Retention test log loss | 0.35854 | 0.35670 |
| Retention test Brier score | 0.11320 | 0.11279 |
| Retention test accuracy | 84.69% | 84.89% |
| Retention test AUC | 0.88732 | 0.88699 |
| 测试集十分钟 formation | 0.22373 | $0.22870\pm0.00202$ |
| 测试集十分钟 retention | 0.77615 | $0.77069\pm0.00313$ |

Same ASN 使独立测试 retention log loss 降低 0.00184、Brier score 降低
0.00041，accuracy 提升 0.20 个百分点；AUC 基本不变并下降 0.00033。因此它带来
的是较小的概率校准改善，而不是显著的排序能力改善。

五个随机种子都生成六个有效的 300 节点快照。所有随机种子和时间步均只有一个
连通分量、没有孤立节点，平均 degree 为 14.20--15.53，最大 degree 为 33--48，
低于 64 的安全上限；边数为 2,130--2,329。相对于三个独立测试日，生成图的平均
retention 误差为 0.00546，formation 误差为 0.00497。随机种子间标准差较小，
说明结果不是某一次随机图抽样的偶然现象。
