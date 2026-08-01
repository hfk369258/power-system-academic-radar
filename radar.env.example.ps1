# Copy this file to radar.env.ps1 and fill one provider block.
# Keep radar.env.ps1 local because it contains secrets.

# IEEE Xplore API, optional:
# $env:IEEE_XPLORE_API_KEY = ""

# Elsevier / Scopus API, optional:
# $env:ELSEVIER_API_KEY = ""

# Semantic Scholar API key, optional but helps avoid HTTP 429 rate limits:
# $env:SEMANTIC_SCHOLAR_API_KEY = ""

# DeepSeek API key, optional; enables detailed Chinese literature interpretation:
# $env:DEEPSEEK_API_KEY = ""

# QQ Mail:
# $env:RADAR_SMTP_HOST = "smtp.qq.com"
# $env:RADAR_SMTP_PORT = "465"
# $env:RADAR_SMTP_USER = "your_qq_number@qq.com"
# $env:RADAR_SMTP_PASSWORD = "your_smtp_authorization_code"
# $env:RADAR_EMAIL_FROM = "your_qq_number@qq.com"
# $env:RADAR_EMAIL_TO = "target@example.com"

# 163 Mail:
# $env:RADAR_SMTP_HOST = "smtp.163.com"
# $env:RADAR_SMTP_PORT = "465"
# $env:RADAR_SMTP_USER = "your_name@163.com"
# $env:RADAR_SMTP_PASSWORD = "your_smtp_authorization_code"
# $env:RADAR_EMAIL_FROM = "your_name@163.com"
# $env:RADAR_EMAIL_TO = "target@example.com"

# WeChat delivery through WeCom robot, ServerChan, PushPlus, or another webhook bridge:
# $env:RADAR_WECHAT_WEBHOOK_URL = ""
