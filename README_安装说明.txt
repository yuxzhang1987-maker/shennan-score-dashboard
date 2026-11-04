深南电路量化因子模型 V1.0（增量升级包）
=====================================

本包是基于 GitHub 仓库当前 main 分支 app.py / update_data.py 结构制作的增量补丁。
它不会覆盖 data/，不会删除或改写 history.csv、hourly_history.csv、alert_state.json 等旧数据。

包内文件：
1. quant_factor_model.py：六因子计算、历史收益统计。
2. apply_quant_upgrade.py：将独立量化因子页面追加到 app.py 末尾。
3. README_安装说明.txt：安装说明。

安装：
1. 先从 GitHub 下载当前整个仓库 ZIP 备份并解压。
2. 将 quant_factor_model.py 和 apply_quant_upgrade.py 放到项目根目录
   （与 app.py、update_data.py 同一层）。
3. 在该文件夹地址栏输入 cmd 并回车。
4. 运行：python apply_quant_upgrade.py
5. 运行：python -m py_compile app.py quant_factor_model.py
6. 将修改后的 app.py 和新增的 quant_factor_model.py 上传 GitHub，
   不要上传或覆盖 data/ 文件夹。
7. 等 Streamlit 页面更新后，进入页面下方的“深南电路量化因子模型 V1.0”，
   点击“计算/刷新量化因子与历史回测”。

模型结构（权重合计100%）：
- 价格动量 20%
- PCB相对强弱 20%
- 成交量价格配合 15%
- 资金流 15%
- 波动风险 15%
- 基本面人工代理 15%

重要限制：
- 基本面目前复用 data/ai_signal.txt 人工信号；未录入时不计入覆盖率。
- 历史回测使用现有行情源能提供的历史长度（当前行情接口通常约600个自然日范围，
  并非承诺3-5年）；请在界面核对样本数和日期。
- 资金流历史长度取决于AKShare/东方财富接口返回结果；不足时会显示覆盖率下降。
- 未来5/20/60日收益仅用于历史事后统计，不参与同日评分，避免前视偏差。
- 这是规则化研究模型，不是上涨概率、预测承诺或自动买卖信号。
- 本版本不改变原有综合评分权重和原有历史CSV。
