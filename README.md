# 深南电路每日评分网页部署包

## 发布网页的最少步骤
1. 登录 GitHub，创建一个新的 Public repository，例如 `shennan-score-dashboard`。
2. 解压本 ZIP，把所有文件（包括隐藏目录 `.github`）上传到仓库根目录，确认 `app.py` 位于根目录。
3. 打开 GitHub 仓库的 Actions 标签页，若提示启用 Actions，请点击启用。
4. 打开 https://share.streamlit.io/ ，使用 GitHub 登录，点击 Create app。
5. 选择仓库、分支 main、主文件路径 app.py，点击 Deploy。部署完成后 Streamlit 会提供一个网页 URL。
6. 在 GitHub Actions 里手动运行 `Daily Shennan Score Update`，先测试是否能下载行情并写入 `data/history.csv`。

## 每日更新与数据累积
- 工作流在工作日 17:30（台湾/中国时间）尝试更新历史评分并提交到仓库。
- GitHub Actions 的计划任务可能延迟；免费行情源可能失败或数据延迟。
- 网页左侧的刷新按钮可尝试即时抓取，但云端运行环境不保证其本地文件持久保存；长期历史以工作流提交到 GitHub 的 CSV 为准。
- AI资本开支信号默认中性 0；如需修改，将 `data/ai_signal.txt` 改为 -2、-1、0、1 或 2 并提交。
- 评分权重：SOX 35%、NDX 15%、美国10年期收益率 20%、深南电路 20%、AI资本开支 10%。缺失指标按可用权重重新归一化。
- 分数是规则模型环境评分，不是上涨概率或买卖指令。
