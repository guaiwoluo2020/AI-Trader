# Disposable Email Domain Blocklist

`disposable_email_blocklist.conf` is sourced from:

https://github.com/disposable-email-domains/disposable-email-domains

The upstream list is public-domain licensed and is maintained for blocking
temporary and disposable email domains. Refresh the local copy with:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/disposable-email-domains/disposable-email-domains/main/disposable_email_blocklist.conf \
  -o resources/disposable_email_blocklist.conf
```
