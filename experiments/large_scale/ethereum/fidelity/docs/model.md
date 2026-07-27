# Xatu-Calibrated Beacon Topology Model

This report separates definitions from fitted results. Parts I--III define the
observation model, estimators, graph generator, and evaluation criteria without
using the fitted values to justify those definitions. Part IV then reports what
ten consecutive Xatu days imply under those assumptions.

## Part I. Observations and Definitions

### 1. What Xatu Observes

#### 1.1 Observation Boundary and One-Minute Snapshots

Parameter estimation uses ten consecutive Xatu Mainnet partitions from
2026-07-07 through 2026-07-16. The chronological split assigns five days to
training, two days to feature validation, and three days to final testing.
Observer identities are selected only from the fitting interval. The final
train-plus-validation refit retains 26 observers that are bounded on at least
80% of fitting days and are never classified as bulk probes.

Xatu directly observes only edges with an observer endpoint. If observer $O$
sees peers $A$ and $B$, it establishes $O-A$ and $O-B$, but it contains no
observation of whether $A-B$ exists. A hidden graph with $A-B$ and one without
$A-B$ therefore produce the same Xatu record. Remote-to-remote adjacency is
not identifiable from these observations alone.

The model consequently does not claim to recover the Mainnet adjacency
matrix or its remote-peer degree distribution. It estimates measurable edge
appearance and persistence behavior on bounded observer-to-remote edges, then
uses those behaviors as a symmetric rule for generating a synthetic graph.
Observer degree is not used as ordinary-node degree because it is partly set
by the measurement software and probing policy.

Let:

- $o$ denote a bounded observer;
- $i$ denote a remote peer;
- $t$ denote a one-minute time bucket;
- $B$ denote the bounded observers present in a fitted day;
- $T$ denote the total number of one-minute buckets;
- $\Delta=10$ minutes denote the topology transition interval.

Define the observed observer-to-remote edge:

$$
e_{oit}=
\begin{cases}
1,&\text{observer }o\text{ is connected to peer }i\text{ in bucket }t,\\
0,&\text{otherwise}.
\end{cases}
$$

All model statistics are derived from this binary variable. Xatu directly
observes observer-to-remote edges. The model learns formation and persistence
behavior from those edges and applies the learned behavior symmetrically to
remote-to-remote pairs.

Comparing two observations separated by $\Delta$ gives four interval-level
edge outcomes:

| State at $t$ | State at $t+\Delta$ | Observed outcome |
|---:|---:|---|
| 0 | 0 | remains unobserved |
| 0 | 1 | formation/appearance |
| 1 | 1 | retention |
| 1 | 0 | dissolution |

A $0\rightarrow1$ outcome means that an observed edge appeared somewhere in
the interval; it does not identify the exact connection time. Likewise, zero
means "not observed by this observer," not necessarily "peer offline."

One minute is the discretization resolution and ten minutes is the chosen
churn horizon. Neither is an Ethereum protocol constant. The ten-minute value
balances nearly unchanged short intervals against long intervals that can hide
several connect/disconnect events. A robustness analysis should repeat the fit
at alternative horizons such as 5, 20, and 30 minutes.

For fitted day $d$, the observed availability of peer $i$ is:

$$
a_{id}=
\frac{1}{T_d}
\sum_{t\in d} I\left(\sum_o e_{oidt}>0\right).
$$

A bucket contributes one whenever at least one bounded observer sees peer $i$.
Thus, $a_i$ measures how persistently the peer is observed over the day. It is
an uptime proxy rather than exact uptime because an online peer with no
observer connection is not visible.

The observer coverage of peer $i$ on day $d$ is:

$$
v_{id}=
\frac{1}{B_d}
\sum_o I\left(\sum_{t\in d} e_{oidt}>0\right).
$$

$v_{id}$ measures how many distinct observers see the peer at least once that
day. The implementation averages these daily covariates with day exposure
weights before Ridge fitting.

#### 1.2 Event Counts and Exposure

An exposure is one opportunity for an event to be observed, and therefore
forms the denominator of an empirical rate. Event counts without exposures are
misleading. For example, a peer retained 800 times in 1,000 tests provides
more evidence than a peer retained twice in two tests, even though the latter
has an apparent retention rate of 100%.

The model uses three exposure concepts.

**Formation opportunity**

The formation opportunities of peer $i$ are approximated separately inside
each fitted day and then added:

$$
O_i=
\sum_{d\in\mathcal D_i}
\max(1,a_{id}T_dB_d).
$$

$a_{id}T_d$ estimates the buckets in which the peer is available on day $d$,
and multiplying by $B_d$ gives observer-bucket opportunities. Daily terms are
not joined across midnight, so no transition treats the end of one partition
as adjacent to the next. The maximum prevents a zero denominator.

For example, if a peer is visible during 100 one-minute buckets and there are
26 bounded observers, the approximation supplies $100\times26=2{,}600$
observer-bucket opportunities. This does not assert that all 26 observers
actually knew the peer's address. It uses visibility to at least one observer
as a proxy for availability to the measurement system.

The new-edge event count is:

$$
F_i=
\sum_{o,t\ge\Delta}
e_{oit}(1-e_{oi,t-\Delta}).
$$

An event is counted when edge $(o,i)$ exists at $t$ but did not exist ten
minutes earlier. Therefore, $F_i/O_i$ is a new-connection frequency per
availability opportunity.

The ideal formation denominator would contain every online, known, currently
unconnected candidate available to each observer. Xatu does not log that risk
set, failed dials, or online peers that never connect. Consequently $O_i$ and
$F_i/O_i$ are approximate, and the latter is used as a relative peer
formation propensity rather than an absolute Mainnet dialing probability.

**Retention exposure**

An edge present at $t$ can be tested for ten-minute retention only if
$t+\Delta$ lies inside the data interval. The retention exposures of peer $i$
are:

$$
E_i=
\sum_{o,t:\,t+\Delta\le T}e_{oit}.
$$

Every qualifying active edge is one exposure. The number that remain present
ten minutes later is:

$$
R_i=
\sum_{o,t:\,t+\Delta\le T}
e_{oit}e_{oi,t+\Delta}.
$$

Consequently, $R_i/E_i$ is the ten-minute retention rate of peer $i$. If an
edge is present at 12:00, 12:01, and 12:02, those buckets provide three
retention exposures because each can be tested against its own ten-minute
future state.

For example, if three edges are present at 12:00 and two remain at 12:10, then
$E=3$, $R=2$, and the interval retention rate is $2/3$. Unlike formation, the
retention risk set is directly observed: every currently active edge can be
tested against its future state. Retention is therefore better identified
than formation.

Overlapping one-minute tests reuse parts of the same sessions, so they are not
independent samples. They provide valid rate point estimates, but uncertainty
must use observer/time-block resampling or non-overlapping windows rather than
a naive independent-binomial confidence interval.

#### 1.3 Two Different Formation Quantities

The model deliberately separates peer allocation from global churn volume.
The peer-specific quantity

$$
\frac{F_i}{O_i}
$$

answers which peers are relatively more likely to acquire an observed edge.
It contributes to the node effect $q_i$. The transition-level quantity

$$
\rho_C=\frac{\sum_iF_i}{\sum_iE_i}
$$

answers how many new edges should be formed per current edge in one transition.
It determines the total formation volume of the generated graph. Using current
edge exposure makes this volume scale with graph size without pretending that
Xatu reveals the complete remote-to-remote non-edge candidate set.

## Part II. Statistical and Graph-Generation Model

### 2. Empirical-Bayes Node Propensities

The raw ratios $F_i/O_i$ and $R_i/E_i$ are unstable for peers with little
exposure. A peer with two successful tests should not be treated as stronger
evidence than a peer with 800 successes in 1,000 tests. The model therefore
shrinks sparse peer estimates toward a global rate while allowing well-observed
peers to retain their individual behavior.

#### 2.1 Formation Smoothing and Formation Effect

The global per-opportunity formation rate is:

$$
\rho_F=
\frac{\sum_iF_i}{\sum_iO_i}.
$$

The model places a Beta prior with mean $\rho_F$ and concentration 20 on the
latent formation probability:

$$
p_i^F\sim
\operatorname{Beta}
\left(20\rho_F,20(1-\rho_F)\right).
$$

After $F_i$ observed formations in $O_i$ approximate opportunities, the
posterior mean is:

$$
\widehat\rho_{F,i}
=
\frac{F_i+20\rho_F}{O_i+20}.
$$

Equivalently:

$$
\widehat\rho_{F,i}
=
\frac{O_i}{O_i+20}\frac{F_i}{O_i}
+
\frac{20}{O_i+20}\rho_F.
$$

This form shows the reason for the estimator: it is a weighted average of the
peer's raw rate and the global rate. Twenty represents 20 global-average
pseudo-opportunities. It is a variance-control hyperparameter, not an Ethereum
constant.

The raw formation effect is the clipped log rate ratio:

$$
q_i^{raw}
=
\operatorname{clip}
\left(
\log\frac{\widehat\rho_{F,i}}{\rho_F},
-6,6
\right).
$$

$q_i^{raw}=0$ means the peer matches the global rate.
$q_i^{raw}=\log2$ means approximately twice the global rate, and
$q_i^{raw}=-\log2$ means approximately half. Formation is a sparse count
event, for which a Poisson intensity naturally uses a log link. For small
probabilities, $\operatorname{logit}(p)\approx\log p$, so the log rate ratio
also serves as an approximate additive offset in the later logistic model.
Clipping prevents sparse observations from creating extreme scores.

#### 2.2 Retention Smoothing and Persistence Effect

The global ten-minute retention rate is:

$$
\rho_R=
\frac{\sum_iR_i}{\sum_iE_i}.
$$

The retention prior has mean $\rho_R$ and concentration 50:

$$
p_i^R\sim
\operatorname{Beta}
\left(50\rho_R,50(1-\rho_R)\right).
$$

Its posterior mean is:

$$
\widehat\rho_{R,i}
=
\frac{R_i+50\rho_R}{E_i+50}
=
\frac{E_i}{E_i+50}\frac{R_i}{E_i}
+
\frac{50}{E_i+50}\rho_R.
$$

Thus a peer with few tests stays near the global retention rate, whereas a
peer with many tests is governed by its own data. The concentrations 20 and 50
must be justified by held-out or sensitivity analysis; the current values are
regularization choices rather than theoretically unique constants.

The raw persistence effect is a log-odds difference:

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

where:

$$
\operatorname{logit}(p)=\log\frac{p}{1-p}.
$$

Because logistic regression satisfies
$\operatorname{logit}(P)=\text{linear score}$, this difference can be inserted
directly as an additive retention effect. Positive values indicate more stable
edges than the global baseline; negative values indicate less stable edges.

#### 2.3 Session Exposure

A heartbeat session is a continuous observed interval for the same
observer-peer connection. For peer $i$, let:

- $N_i$ be the usable session count;
- $L_{is}$ be the observed duration of session $s$;
- $T_i^{session}$ be total observed session time;
- $D_i$ be the count of observed session endings;
- $M_i$ be the median observed session duration.

Total session exposure is:

$$
T_i^{session}
=
\sum_{s=1}^{N_i}\max(L_{is},1\text{ minute}).
$$

The one-minute floor matches the source time resolution and prevents a
zero-duration session from contributing no exposure. Session exposure is time
at risk. Retention exposure is the number of ten-minute survival tests.

#### 2.4 Censored Sessions and Lifetime Estimation

A session already active at the beginning of the data is left-censored because
its start time is unknown. Such sessions are excluded. A session still active
at the end is right-censored. Its observed time contributes to
$T_i^{session}$, but it does not increment $D_i$ because no ending was
observed.

Under an exponential lifetime model with approximately constant termination
hazard $\lambda$, the likelihood depends on observed endings and total
exposure:

$$
L(\lambda)\propto
\lambda^{\sum_iD_i}
\exp\left(-\lambda\sum_iT_i^{session}\right).
$$

Differentiating the log likelihood and setting it to zero gives:

$$
\frac{\partial\log L}{\partial\lambda}
=
\frac{\sum_iD_i}{\lambda}
-\sum_iT_i^{session}
=0.
$$

The maximum-likelihood termination rate and mean lifetime are therefore:

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

This derivation explains why a right-censored session contributes observed
time but not an ending. A connection alive for eight hours at the data
boundary says that its lifetime is at least eight hours, not exactly eight
hours.

For a peer with few sessions, $T_i^{session}/D_i$ is unstable and is infinite
when $D_i=0$. The model adds empirical-Bayes pseudo-data equivalent to ten
globally average completed sessions:

$$
\widetilde M_i=
\frac{T_i^{session}+10M_{global}}{D_i+10}.
$$

The numerator adds ten global-mean lifetimes and the denominator adds ten
endings. Real observations dominate when $D_i$ is large, while sparse peers
shrink toward $M_{global}$. The value 10 is a regularization strength, not a
protocol constant.

#### 2.5 Ridge Partial Pooling

Low-exposure raw effects remain noisy. The model predicts a regularized effect
from node attributes. Its continuous feature vector is:

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

Logit maps proportions from $(0,1)$ to the real line. $\log(1+x)$ compresses
long-tailed counts and durations so a few extreme sessions do not dominate.
Each continuous feature is standardized:

$$
z_{ik}=
\frac{u_{ik}-\overline u_k}{s_k}.
$$

Standardization makes the ridge penalty comparable across numeric features.
The full design vector is:

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

One-hot encoding retains the 8 most frequent clients, 10 countries, 16 ASNs,
and 12 organizations. This avoids fitting coefficients for categories with
only one or two samples. These are node-level covariates for $q_i$ and $r_i$,
not additional pair variables.

Let:

$$
P=\operatorname{diag}(0,1,\ldots,1).
$$

The leading zero leaves the intercept unpenalized. The ridge objectives are:

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

Differentiating gives the closed-form ridge solution:

$$
\widehat\theta
=
\left(X^TX+10P\right)^{-1}X^Ty.
$$

The quadratic penalty shrinks large coefficients. It is equivalent to a
zero-mean Gaussian prior on non-intercept coefficients and reduces
multicollinearity and overfitting from sparse one-hot features. Ridge
predictions are:

$$
q_i^{reg}=X_i^T\widehat\theta_q,
\qquad
r_i^{reg}=X_i^T\widehat\theta_r.
$$

Direct estimates and ridge predictions are mixed according to evidence:

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

When $F_i=0$, the formation effect comes entirely from ridge. At $F_i=12$,
the direct and ridge estimates have equal weight. Large $F_i$ makes the direct
estimate dominant. The retention weight has the same interpretation with 120
exposures. This is empirical-Bayes partial pooling: sparse peers borrow group
information, while well-observed peers retain individual behavior.

Final effects are median-centered, standardized, and clipped:

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

Median centering resists extreme peers, standardization puts $q_i$ and $r_i$
on comparable scales, and clipping limits the ability of one peer to dominate
an edge probability. The transformation preserves the relative order of most
peers.

### 3. Same-ASN Pair Effect

#### 3.1 Candidate Pair Variables and Logistic Probability

Pair-feature screening considers same ASN, organization, country, and city.
For the equations below, $S_{ij}$ denotes the binary feature retained by the
time-split selection procedure. Part IV reports which candidate is retained.
For same ASN, the definition is:

$$
S_{ij}=I(ASN_i=ASN_j).
$$

$S_{ij}=1$ when both non-empty ASN values match, and zero otherwise.
Probabilities are obtained from:

$$
\sigma(z)=\frac{1}{1+\exp(-z)}.
$$

Its odds satisfy:

$$
\frac{\sigma(z)}{1-\sigma(z)}=\exp(z).
$$

Adding coefficient $\beta$ to a linear score therefore multiplies event odds
by $\exp(\beta)$.

#### 3.2 Formation Coefficient

Formation samples at most 10,000 matched rankings per day. Each sample contains
observer $o$, the newly connected peer $i$, and a control peer $j$ that was
available in the same bucket but not connected to $o$. Their ranking
probability is:

$$
P(i\succ j)
=
\sigma
\left[
(q_i-q_j)
+\beta(S_{oi}-S_{oj})
\right].
$$

This is conditional logistic regression, equivalently a Bradley-Terry
pairwise ranking model. If a peer has selection score
$q_i+\beta S_{oi}$, comparing peers $i$ and $j$ cancels the observer and
time-bucket baseline, leaving the score difference above.

Formation has no fitted intercept and uses logistic maximum likelihood with
$L_2$ strength 2:

$$
\widehat\beta
=
\arg\min_\beta
\left[
-\sum_m\log P(i_m\succ j_m)
+\frac{\lambda}{2}\beta^2
\right].
$$

The likelihood rewards assigning a higher probability to the observed new
connection. The quadratic term prevents finite samples from producing an
excessive coefficient. The implementation uses $\lambda=2$, so the penalty
value is $\beta^2$ and its gradient is $2\beta$, matching the fitter.

#### 3.3 Retention Coefficient

For an observer-peer edge active at $t$, define:

$$
y_{oit}=
\begin{cases}
1,&e_{oi,t+\Delta}=1,\\
0,&e_{oi,t+\Delta}=0.
\end{cases}
$$

Its retention probability is:

$$
P(y_{oit}=1)
=
\sigma(c+r_i+\gamma S_{oi}).
$$

$c$ is the global baseline retention log odds, $r_i$ is the peer persistence
effect, and $\gamma$ is the additional same-ASN effect after controlling for
$r_i$. The model samples at most 10,000 active-edge exposures per day. Let
$p_m=\sigma(c+r_{i_m}+\gamma S_{o_mi_m})$. The objective is:

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

This is Bernoulli negative log likelihood plus ridge regularization. The
intercept remains unpenalized so it can fit global retention, while $\gamma$
is penalized to control variance in the same-ASN effect.

The fitted values of $\beta$ and $\gamma$, their odds multipliers, and their
held-out evidence are reported together in Part IV. Keeping them out of this
definition section prevents a fitted association from being mistaken for an
assumed rule.

Pair variables are fitted on five complete training days. A variable must have
the same coefficient sign in the early and late training-day groups and reduce
log loss by at least $5\times10^{-4}$ on two later validation days. The selected
feature set is then refitted on all seven fitting days. Three still-later test
days are scored exactly once and do not affect node effects, observer selection,
Ridge coefficients, pair-feature selection, or pair coefficients.

Transferring observer-to-remote coefficients to remote-to-remote pairs uses a
conditional exchangeability assumption: after conditioning on node
propensities and same-ASN status, observer identity does not reverse the
formation or retention effect. This assumption makes the measured behavior
usable in a symmetric P2P graph.

### 4. Initial Graph Generation

#### 4.1 Graph Variables

Let $n$ be the configured generated-node count. Using a fixed random seed,
nodes are sampled from peers visible to bounded observers in the selected
initial bucket, so every generated peer has evidence of availability at that
time. Part IV reports the current $n$, seed, bucket, and candidate count.

Define the undirected graph variable:

$$
x_{ij}^{(t)}=
\begin{cases}
1,&\text{snapshot }t\text{ contains edge }(i,j),\\
0,&\text{otherwise}.
\end{cases}
$$

Node degree is:

$$
d_i^{(t)}
=
\sum_{j\ne i}x_{ij}^{(t)}.
$$

By the handshaking lemma, each undirected edge contributes one degree to each
endpoint:

$$
\overline d_t
=
\frac{1}{n}\sum_i d_i^{(t)}
=
\frac{2|E_t|}{n}.
$$

#### 4.2 Symmetric Pair Scores

The formation score is:

$$
f_{ij}=q_i+q_j+\beta S_{ij}.
$$

$q_i+q_j$ symmetrically extends observer-target ranking to two ordinary remote
peers, allowing both endpoint formation propensities to contribute. The final
term adds the measured same-ASN formation correction.

The retention score is:

$$
h_{ij}
=
\frac{r_i+r_j}{2}
+\gamma S_{ij}.
$$

Retention training observes one remote endpoint per sample. Averaging
$r_i$ and $r_j$ includes both generated endpoints while preserving the scale
of the single-remote $r_i$ used during training.

The initial edge score is:

$$
z_{ij}^{(0)}
=
f_{ij}+h_{ij}
=
q_i+q_j
+\frac{r_i+r_j}{2}
+(\beta+\gamma)S_{ij}.
$$

No supervised remote-to-remote initial-edge labels are available. The combined
score therefore applies a generation criterion that favors edges that are both
easy to form and likely to persist. Adding the scores is consistent with
additive logistic log odds, but it is a multi-objective generation rule rather
than a classifier fitted to hidden adjacency labels.

The initial edge probability is:

$$
p_{ij}^{(0)}
=
\sigma(c_0+z_{ij}^{(0)}).
$$

$c_0$ controls global density without changing pair ranking. Allowed mean
degree is:

$$
6\le\overline d\le24.
$$

The calibration target is the interval midpoint:

$$
d^*=\frac{6+24}{2}=15.
$$

Define mean pair probability:

$$
\overline p(c_0)
=
\frac{1}{\binom n2}
\sum_{i<j}\sigma(c_0+z_{ij}^{(0)}).
$$

For a conditionally independent Bernoulli graph:

$$
\mathbb E[\overline d]
=
\frac{2}{n}\sum_{i<j}p_{ij}^{(0)}
=(n-1)\overline p(c_0).
$$

Setting expected mean degree to $d^*$ gives:

$$
\overline p(c_0)=\frac{d^*}{n-1}.
$$

Because $\overline p(c_0)$ is monotone in $c_0$, binary search gives a unique
calibrated intercept. Pairs are then sampled independently:

$$
x_{ij}^{(0)}
\sim
\operatorname{Bernoulli}(p_{ij}^{(0)}).
$$

Conditional Bernoulli sampling is the basic covariate-driven random graph
model. Connectivity and peer-cap constraints applied afterward introduce
dependence between final edges. The generator makes up to 20 attempts and
accepts a graph inside the broad mean-degree interval without assigning a
per-node degree target.

### 5. Ten-Minute Topology Transitions

#### 5.1 Existing-Edge Retention

For current edge $(i,j)\in E_t$, retention probability is:

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

Node persistence and same-ASN status determine relative edge stability. The
dynamic intercept $c_t^{keep}$ calibrates the mean probability across current
edges to the measured global rate:

$$
\frac{1}{|E_t|}
\sum_{(i,j)\in E_t}
p_{ij}^{keep}(t)
=
\rho_R.
$$

Sigmoid monotonicity allows binary-search calibration. Each existing edge then
undergoes one Bernoulli retention trial. Calibration preserves relative
heterogeneity from $r_i$ and same ASN while matching the measured $\rho_R$.

#### 5.2 Non-Edge Formation

The transition formation ratio differs from the per-opportunity rate used to
estimate node effects. It is:

$$
\rho_C=
\frac{\sum_iF_i}{\sum_iE_i}.
$$

The denominator is active-edge exposure, so $\rho_C$ is the number of new
edges expected per current edge during one transition.

For $|E_t|$ current edges, the target additions are:

$$
A_t^*=\rho_C|E_t|.
$$

Let $U_t$ be the set of current non-edges. Their formation probabilities are:

$$
p_{ij}^{form}(t)
=
\sigma(c_t^{form}+q_i+q_j+\beta S_{ij}),
\qquad
(i,j)\in U_t.
$$

The dynamic intercept satisfies:

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

The right side is target additions divided by available non-edges. The 0.95
cap prevents a tiny graph from requiring nearly every non-edge to form and
keeps the logistic intercept finite. By linearity of expectation:

$$
\mathbb E[|E_{t+1}\setminus E_t|]
=
\sum_{(i,j)\in U_t}p_{ij}^{form}(t)
\approx A_t^*.
$$

#### 5.3 Why Edge Count Remains Stable

Ignoring peer-cap and connectivity repairs:

$$
\mathbb E[|E_{t+1}|]
\approx
\rho_R|E_t|
+\rho_C|E_t|
=
(\rho_R+\rho_C)|E_t|.
$$

When the fitted value of $\rho_R+\rho_C$ is near one, the model replaces many
specific neighbors while keeping expected total edge count nearly stable.
This is an empirical consequence to be checked in Part IV, not a fixed-edge-
count constraint. Actual edge count fluctuates under Bernoulli sampling.

Five transitions are generated, each representing ten minutes. Existing-edge
retention and non-edge formation are sampled separately.

### 6. Structural Constraints

Every snapshot is an undirected simple graph:

$$
x_{ij}^{(t)}=x_{ji}^{(t)},
\qquad
x_{ii}^{(t)}=0.
$$

The first equation enforces symmetry and the second excludes self-loops. Edge
sets prevent duplicates.

The safe per-node peer cap is:

$$
d_i^{(t)}
\le
80-16
=64.
$$

The software peer limit is 80 and 16 slots are reserved for runtime,
bootstrap, and transient churn connections. Thus 64 is a safety ceiling, not
a target degree.

Mean degree must remain in:

$$
6\le\overline d_t\le24.
$$

Observer degree is controlled by measurement configuration and cannot identify
ordinary remote-peer degree. The model therefore does not fit or assign exact
per-node degrees. It uses only a broad operational density interval well below
the software limit.

Each graph must contain one connected component and therefore no isolated
nodes. If sampling creates multiple components, the generator repeatedly adds
the highest-probability cross-component edge that respects the peer cap. If a
node exceeds 64 peers, its lowest-probability incident edges are considered
for deletion, and a deletion is accepted only if connectivity remains. These
operational constraints take precedence over independent sampling.

## Part III. Evaluation Definitions

### 7. Model Evaluation

#### 7.1 Churn Error

Generated mean ten-minute retention is:

$$
\widehat\rho_R^{gen}
=
\frac{1}{5}
\sum_{t=1}^{5}
\frac{|E_{t-1}\cap E_t|}{|E_{t-1}|}.
$$

Generated mean ten-minute formation is:

$$
\widehat\rho_C^{gen}
=
\frac{1}{5}
\sum_{t=1}^{5}
\frac{|E_t\setminus E_{t-1}|}{|E_{t-1}|}.
$$

Absolute errors are:

$$
\epsilon_R=
\left|\widehat\rho_R^{gen}-\rho_R\right|,
\qquad
\epsilon_C=
\left|\widehat\rho_C^{gen}-\rho_C\right|.
$$

They directly measure deviation from the Xatu-calibrated churn targets.

#### 7.2 Held-Out Log Loss

For binary outcome $y_m\in\{0,1\}$ and predicted probability $p_m$:

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

Log loss is Bernoulli average negative log likelihood and a strictly proper
scoring rule. It evaluates probability quality and heavily penalizes confident
wrong predictions. A value below the no-pair-effect baseline means same ASN
adds held-out probability information.

Accuracy thresholds probability at 0.5:

$$
\operatorname{Accuracy}
=
\frac{1}{M}
\sum_m
I\left(I(p_m\ge0.5)=y_m\right).
$$

Accuracy is easier to interpret but sensitive to class imbalance, so model
selection primarily uses log loss.

The Brier score, $M^{-1}\sum_m(p_m-y_m)^2$, measures squared probability error;
lower is better. AUC is the probability that a randomly selected retained edge
receives a higher score than a randomly selected dissolved edge. AUC measures
ranking rather than probability calibration, so it may remain unchanged even
when log loss and Brier score improve.

#### 7.3 Node-Attribute Distribution

After sampling $n$ nodes from the initial candidate population, total variation
distance checks whether client and country distributions changed:

$$
\operatorname{TV}(P,Q)
=
\frac{1}{2}
\sum_k|P_k-Q_k|.
$$

$P_k$ is category $k$ frequency in the candidate population and $Q_k$ is its
frequency in selected nodes. TV 0 means identical distributions and TV 1
means disjoint distributions. This evaluates node-sampling representativeness,
not edge prediction.

## Part IV. Fitted Results

### 8.1 Temporal Split and Fitted Churn Rates

The final model uses Xatu Mainnet partitions from 2026-07-07 through
2026-07-16. Training uses July 7--11, validation uses July 12--13, and the
independent test interval is July 14--16. Test events and observer behavior are
not used for any selection or refit. The final seven-day fitting interval
contains 17,667 remote peers and retains 26 stable bounded observers. Of test
pair endpoints, 96.90% in formation ranking and 95.99% in retention were seen
during fitting; unseen peers use the global zero-effect prior.

The final train-plus-validation rates are:

$$
\rho_F=0.010299,
\qquad
\rho_R=0.766464,
\qquad
\rho_C=0.233628.
$$

Thus $\rho_R+\rho_C=1.000092$, so the fitted expected edge count is nearly
stable without imposing a fixed edge count. The three test days independently
show $\rho_R=0.776148$ and $\rho_C=0.223733$. Expanding-window one-day-ahead
errors on the three test dates range from 0.0035 to 0.0116 for retention and
from 0.0034 to 0.0117 for formation.

### 8.2 Fitted Pair Effects

No pair feature passes validation for formation. In particular, the negative
same-ASN formation coefficient found by the one-day prototype does not improve
the two-day validation loss and is set to zero. This is evidence that the old
formation result was not temporally robust.

Retention retains only same ASN. After the final seven-day refit:

$$
\gamma=0.4752,
\qquad
\exp(\gamma)=1.608.
$$

After controlling for node persistence, a same-ASN observed edge therefore has
about 1.61 times the ten-minute retention odds of a different-ASN edge. Country,
city, and organization effects have stable positive training signs but do not
provide enough additional validation improvement once same ASN is present.

### 8.3 Independent Test and Generated-Graph Results

| Metric | Baseline or test observation | Final model |
|---|---:|---:|
| Formation test log loss | 0.45626 | 0.45626 |
| Formation test accuracy | 76.23% | 76.23% |
| Retention test log loss | 0.35854 | 0.35670 |
| Retention test Brier score | 0.11320 | 0.11279 |
| Retention test accuracy | 84.69% | 84.89% |
| Retention test AUC | 0.88732 | 0.88699 |
| Test ten-minute formation | 0.22373 | $0.22870\pm0.00202$ |
| Test ten-minute retention | 0.77615 | $0.77069\pm0.00313$ |

Same ASN reduces independent retention log loss by 0.00184 and Brier score by
0.00041. Accuracy rises by 0.20 percentage points, while AUC is essentially
unchanged and decreases by 0.00033. The result is therefore a small probability-
calibration improvement, not a large ranking improvement.

All five random seeds generate six valid 300-node snapshots. Across every seed
and time step, graphs contain one connected component, no isolated node, mean
degree 14.20--15.53, and maximum degree 33--48, below the safe cap of 64. Edge
counts range from 2,130 to 2,329. Relative to the independent test days, the
generated mean retention error is 0.00546 and the generated mean formation
error is 0.00497. Seed standard deviations are small enough to show that these
results are not an artifact of one random graph draw.
