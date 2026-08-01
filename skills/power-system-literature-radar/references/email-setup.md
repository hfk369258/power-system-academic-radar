# Email Setup

The radar sends email through SMTP. Put secrets in `radar.env.ps1`, not in JSON config.

## QQ Mail

Use an SMTP authorization code, not your QQ password.

```powershell
$env:RADAR_SMTP_HOST = "smtp.qq.com"
$env:RADAR_SMTP_PORT = "465"
$env:RADAR_SMTP_USER = "your_qq_number@qq.com"
$env:RADAR_SMTP_PASSWORD = "your_smtp_authorization_code"
$env:RADAR_EMAIL_FROM = "your_qq_number@qq.com"
$env:RADAR_EMAIL_TO = "target@example.com"
```

## 163 Mail

Use an SMTP authorization code, not your mailbox password.

```powershell
$env:RADAR_SMTP_HOST = "smtp.163.com"
$env:RADAR_SMTP_PORT = "465"
$env:RADAR_SMTP_USER = "your_name@163.com"
$env:RADAR_SMTP_PASSWORD = "your_smtp_authorization_code"
$env:RADAR_EMAIL_FROM = "your_name@163.com"
$env:RADAR_EMAIL_TO = "target@example.com"
```

## Outlook / Microsoft 365

Use port 587 with STARTTLS. Some tenants disable SMTP AUTH; if login fails, ask the account administrator whether SMTP AUTH is allowed.

```powershell
$env:RADAR_SMTP_HOST = "smtp.office365.com"
$env:RADAR_SMTP_PORT = "587"
$env:RADAR_SMTP_USER = "your_name@outlook.com"
$env:RADAR_SMTP_PASSWORD = "your_password_or_app_password"
$env:RADAR_EMAIL_FROM = "your_name@outlook.com"
$env:RADAR_EMAIL_TO = "target@example.com"
```

Also set `notifications.email.use_ssl` to `false` and `notifications.email.use_starttls` to `true` in the config.

## Gmail

Use a Google app password when two-step verification is enabled.

```powershell
$env:RADAR_SMTP_HOST = "smtp.gmail.com"
$env:RADAR_SMTP_PORT = "465"
$env:RADAR_SMTP_USER = "your_name@gmail.com"
$env:RADAR_SMTP_PASSWORD = "your_google_app_password"
$env:RADAR_EMAIL_FROM = "your_name@gmail.com"
$env:RADAR_EMAIL_TO = "target@example.com"
```

## Multiple Recipients

Separate recipients with commas:

```powershell
$env:RADAR_EMAIL_TO = "first@example.com,second@example.com"
```
