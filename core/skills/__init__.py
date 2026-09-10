"""skills 包：预置归因与分析技能（Analytic Skill Pack）。

技能以"可导入库 + 沙箱可执行脚本"双形态提供，全部只读输入数据、
输出结构化摘要（聚合矩阵 ≤100 行），不做任何网络与数据库访问：

- ``drilldown``：维度下钻定位（熵 / 信息增益：哪个维度-取值组合对指标
  偏差的解释力最强）；
- ``decomposition``：指标分解树（乘法：GMV = UV × CR × AOV 的对数链式
  贡献；加法：分项差额贡献占比）；
- ``timeseries``：时间序列异常与相似性（DTW 距离、Holt-Winters 三次指数
  平滑残差带异常检测）；
- ``shapley``：Shapley 值偏差归因（因子 ≤8 时精确排列法）。

数值栈约束：仅依赖 numpy + 标准库（避免 statsmodels/sklearn 重依赖），
关键算法与已知解析解对拍（见 tests/test_skills.py）。
"""
