# Source Access Notes

Use the least invasive legal access path for each source.

## IEEE Xplore

Preferred:

- IEEE Xplore API key in `IEEE_XPLORE_API_KEY`.
- Manual RIS, BibTeX, or CSV exports from the user's own logged-in IEEE Xplore session.

Avoid:

- Asking for IEEE usernames or passwords.
- Automating password login, CAPTCHA bypass, or bulk PDF downloading.
- Storing browser cookies in the plugin.

Config pattern:

```json
{
  "name": "ieee_xplore",
  "type": "ieee_xplore_api",
  "enabled": true,
  "api_key_env": "IEEE_XPLORE_API_KEY",
  "max_results": 25
}
```

## CNKI

Preferred:

- Manual CNKI exports placed in a watched folder.
- Authorized institutional APIs if the user already has one.

Avoid:

- Password collection.
- CAPTCHA bypass.
- Automated scraping of logged-in search pages.

Manual-export config pattern:

```json
{
  "paths": [
    "manual_exports/cnki",
    "manual_exports/ieee",
    "manual_exports/wos_scopus"
  ],
  "formats": ["ris", "bib", "csv", "txt"]
}
```

## Other Recommended Sources

- OpenAlex: broad public metadata.
- Crossref: DOI and publisher metadata.
- arXiv: control, optimization, and systems preprints.
- Journal or publisher RSS feeds: table-of-contents alerts.
- Web of Science, Scopus, Engineering Village: manual export unless the user has an official API.
- Semantic Scholar: useful if an API key is available; avoid aggressive unauthenticated polling.
- Google Scholar: do not scrape directly; use user-provided exports or a compliant paid API such as SerpAPI if available.

## Credential Pattern

Use environment variables for secrets:

```powershell
$env:IEEE_XPLORE_API_KEY = "..."
$env:RADAR_WEBHOOK_URL = "..."
```

Never commit credentials to config files, output digests, logs, state files, or plugin manifests.
