# Troubleshooting

## Aborts on startup after 30seconds
... with message
```
** (Mix) Could not start application db_portal: exited in: DbPortal.Application.start(:norm
al, [])
    ** (EXIT) exited in: GenServer.call(ExAws.Config.AuthCache, {:refresh_config, %{access_
key_id: [{:system, "AWS_ACCESS_KEY_ID"}, {:awscli, "default", 30}, :instance_role], host: "
s3.amazonaws.com", http_client: ExAwsFinchAdapter, json_codec: Jason, normalize_path: true,
 port: 443, region: "us-east-1", retries: [max_attempts: 10, base_backoff_in_ms: 10, max_ba
ckoff_in_ms: 10000], scheme: "https://", secret_access_key: [{:system, "AWS_SECRET_ACCESS_K
EY"}, {:awscli, "default", 30}, :instance_role]}}, 30000)
        ** (EXIT) time out
```
The issue is that no `~/.aws/credentials` file was found.  Not sure
whether an empty file here will work or not.  I installed `aws` cli (`pip3
install aws`) and also `opsh` and used that to get some credentials.

Comes from the `Vapor.load()` in `application.ex`

## site_encrypt error - might really be port in use eaddrinuse

