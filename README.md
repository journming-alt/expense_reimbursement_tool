# 自动差旅报销工具

这是一个本地运行的差旅报销辅助工具。它不会上传发票数据，不依赖服务器，也不会破解或绕过国家税务总局全国增值税发票查验平台的人机校验。

## 功能

- 通过浏览器控件选择本地出差文件夹
- 自动扫描交通发票、住宿发票、餐票、市内交通费和发票验真结果
- 解析 PDF 文本型发票、OFD 航空电子客票行程单
- OCR 兜底接口已预留，安装 PaddleOCR 后可用于图片型发票
- 已存在验真 PDF 时自动标记为“已查验”
- 复制原 Excel 模板并填写，不覆盖原始 Excel
- 默认只把自动生成的报销 Excel 输出到输入的发票文件夹

## Mac 安装方法

```bash
cd expense_reimbursement_tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
streamlit run app.py
```

如果 PDF 都是文本型、OFD 也能解析，可以先不安装 PaddleOCR。

## Windows 安装方法

```powershell
cd expense_reimbursement_tool
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
streamlit run app.py
```

建议使用 Python 3.11 或更新版本。

## 如何使用

1. 打开本地界面后点击“新建报销”。
2. 点击“选择出差文件夹”，选择包含交通发票、住宿发票、餐票、发票验真结果和差旅报销明细表的文件夹。
3. 输入考试员和考点，点击“开始扫描”。
4. 在“发票识别结果”里核对可编辑表格，必要时手动修正姓名、日期、地点、交通工具、金额、住宿天数等字段。
5. 点击“开始验真”。已有验真 PDF 会自动识别为“已查验”；未验真的发票可点击“打开查验（半自动）”。
   - 浏览器打开后不会自动关闭。请在浏览器中完成人机验证和查验。
   - 页面会提示发票号码、开票日期和金额，方便手动输入。
   - 查验结果出现后，请在浏览器中手动保存 PDF 到当前发票文件夹的“发票验真结果”目录。
   - 保存后回到工具页面点击“我已手动保存 PDF，标记已查验并关闭浏览器”。
6. 点击“生成报销表”，工具会在输入的发票文件夹里输出 `xx差旅报销明细表_自动生成.xlsx`。
7. 在“导出”页面查看自动生成 Excel 路径。

## 税务平台验证码怎么办

工具只自动打开网页并填写可填写字段。遇到验证码、滑块、人机校验时，请在浏览器里手动完成。工具不会破解验证码，不会逆向税务平台接口，也不会绕过校验。查验结果页面出现后，请在浏览器里手动保存 PDF 到“发票验真结果”文件夹，再回到工具页面标记已查验并关闭浏览器。

## Mac Dock 里残留 TEST Chrome 图标怎么办

这是 Playwright 的 `Google Chrome for Testing`。可以在工具的“验真”页面点击“清理残留查验浏览器”；如果仍未消失，再点“强制清理残留浏览器”。

也可以手动处理：右键 Dock 里的 TEST Chrome 图标并退出，或按 `Command + Option + Esc`，选择 `Google Chrome for Testing` 后强制退出。

## OFD 识别失败怎么办

本工具优先把 OFD 当 zip 读取，解析其中的 XML/XBRL 字段。若 OFD 内没有标准 `atr` 字段，识别结果会标记为“需人工处理”。可以在表格中手动补充发票号码、航班号、日期、金额等字段。

## PDF 无法提取文字怎么办

文本型 PDF 会优先使用 `pypdf`，并可 fallback 到 `pdfplumber` 或 `PyMuPDF`。如果 PDF 是扫描件或图片型 PDF，请安装 PaddleOCR，并在识别后人工核对低置信度字段。

## Excel 样式错乱怎么办

工具会复制原始模板，在复制件上填写，并尽量保留合并单元格、边框、字号、公式和布局。若票据数量超过模板预留行，会自动插入行并复制上一行样式。复杂模板建议先用样例模板验证一次。

## 如何更换公司抬头和税号

编辑：

```text
config/company.yaml
```

默认内容：

```yaml
company_name: 中国航空运输协会
tax_id: 51100000500019891R
```

## 吉林01演示测试

默认测试会读取用户提供的 `吉林01.zip`：

```bash
python tests/test_jilin01.py
```

也可以指定 zip 路径：

```bash
JILIN_ZIP_PATH="/path/to/吉林01.zip" python tests/test_jilin01.py
```
```启动
cd /Users/dabao/Documents/fapiao/expense_reimbursement_tool
source .venv311/bin/activate
export PADDLE_PDX_CACHE_HOME=/Users/dabao/Documents/fapiao/expense_reimbursement_tool/.paddlex_cache
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
streamlit run app.py --server.port 8501
