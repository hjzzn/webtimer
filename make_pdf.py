import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def create_deployment_pdf(filename="Ubuntu_Deployment_Guide.pdf"):
    # 1. 创建 PDF 文档对象
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        rightMargin=40, leftMargin=40,
        topMargin=40, bottomMargin=40
    )

    # 2. 设置样式（内置支持多语言安全字体，此处采用标准衬线体系）
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=24,
        leading=28,
        textColor=colors.HexColor("#1A365D"),
        spaceAfter=15
    )

    h2_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'BodyTextCustom',
        parent=styles['BodyText'],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#2D3748"),
        spaceAfter=8
    )

    code_style = ParagraphStyle(
        'CodeStyle',
        parent=styles['Code'],
        fontName='Courier',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#1A202C"),
        backColor=colors.HexColor("#EDF2F7"),
        borderColor=colors.HexColor("#E2E8F0"),
        borderWidth=1,
        borderPadding=6,
        spaceAfter=10
    )

    story = []

    # --- PDF 内容构建 ---
    story.append(Paragraph("Home IoT 系统 - Ubuntu 生产环境部署指南", title_style))
    story.append(
        Paragraph("本文档指导如何将基于 FastAPI + Paho MQTT 的物联网后端项目作为 systemd 守护进程安装至 Ubuntu 系统。",
                  body_style))
    story.append(Spacer(1, 10))

    # 步骤 1
    story.append(Paragraph("第一步：Ubuntu 环境基础准备", h2_style))
    story.append(Paragraph("登录 Ubuntu 服务器，更新系统软件包索引并安装 Python 3 虚拟环境核心组件：", body_style))
    story.append(Paragraph("sudo apt update<br/>sudo apt install python3-pip python3-venv -y", code_style))

    # 步骤 2
    story.append(Paragraph("第二步：项目代码迁移与依赖沙盒安装", h2_style))
    story.append(Paragraph("1. 创建规范化的项目根目录，并将代码、网页及 <b>emqxsl-ca.crt</b> 证书上传至此：", body_style))
    story.append(Paragraph(
        "sudo mkdir -p /var/www/webtimer<br/>sudo chown -R $USER:$USER /var/www/webtimer<br/>cd /var/www/webtimer",
        code_style))
    story.append(
        Paragraph("2. 构建独立的 Python 虚拟环境，并安装经典兼容版核心依赖（免除 VERSION2 报错困扰）：", body_style))
    story.append(Paragraph(
        "python3 -m venv .venv<br/>source .venv/bin/activate<br/>pip install fastapi paho-mqtt \"uvicorn[standard]\"",
        code_style))

    # 步骤 3
    story.append(Paragraph("第三步：编写 Systemd 守护进程服务配置", h2_style))
    story.append(Paragraph("向 Ubuntu 系统服务引擎注册进程管理器，创建配置文件：", body_style))
    story.append(Paragraph("sudo nano /etc/systemd/system/webtimer.service", code_style))
    story.append(Paragraph("在编辑器内输入以下标准生产级配置文本，保存并退出：", body_style))

    service_text = (
        "[Unit]<br/>"
        "Description=Home IoT WebTimer FastAPI & MQTT Service<br/>"
        "After=network.target<br/><br/>"
        "[Service]<br/>"
        "Type=simple<br/>"
        "User=root<br/>"
        "WorkingDirectory=/var/www/webtimer<br/>"
        "ExecStart=/var/www/webtimer/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000<br/>"
        "Restart=always<br/>"
        "RestartSec=5<br/>"
        "StandardOutput=journal<br/>"
        "StandardError=journal<br/><br/>"
        "[Install]<br/>"
        "WantedBy=multi-user.target"
    )
    story.append(Paragraph(service_text, code_style))

    # 步骤 4
    story.append(Paragraph("第四步：激活、加载与开机自启接管", h2_style))
    story.append(Paragraph("刷新 Ubuntu 内核服务链条，激活自启动策略并立刻越行该服务：", body_style))
    story.append(
        Paragraph("sudo systemctl daemon-reload<br/>sudo systemctl start webtimer<br/>sudo systemctl enable webtimer",
                  code_style))

    # 步骤 5
    story.append(Paragraph("第五步：实时生产监控与运维维护", h2_style))
    story.append(Paragraph("使用以下指令对后台静默运行的进程进行全时段健康观测：", body_style))
    story.append(
        Paragraph("<b># 查看服务当前活动状态 (期望为绿色的 active running)</b><br/>sudo systemctl status webtimer",
                  code_style))
    story.append(Paragraph("<b># 动态追踪 Python 的 print 与异步网络心跳日志</b><br/>sudo journalctl -u webtimer -f",
                           code_style))

    # 3. 渲染生成 PDF
    doc.build(story)
    print(f"🎉 完美！PDF 手册已成功生成到当前目录下：{os.path.abspath(filename)}")


if __name__ == "__main__":
    create_deployment_pdf()