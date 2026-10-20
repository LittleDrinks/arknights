# Public gateway with storage in the lab

```text
browser -> Cloudflare -> szh Caddy :80/:443
                            -> 127.0.0.1:48181
                            -> reverse SSH -> lab 127.0.0.1:38181
                                              -> results/collections/
```

The game, collector, assets and recordings stay in `/data1/zsm/arknights`.
The gateway stores configuration only. This deployment retains the pinned game.
The game's ordinary HTTP and WebSocket requests use the same hostname and route.

## Reverse tunnel

`arknights-tunnel.service` runs in the lab user's systemd manager. Install it in
`~/.config/systemd/user/`, then run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now arknights-tunnel.service
systemctl --user status arknights-tunnel.service
```

The lab user needs lingering enabled (`loginctl enable-linger`) to start its user
manager at boot. The tunnel retries after disconnects. The existing game process
must separately be running on port 38181; this unit does not start or restart it.

Private key and trusted host key file live only under the ignored
`runtime/public-proxy/` directory, with mode 0600. Generate a dedicated key in
that directory, and get the gateway's SSH host public key through an already
authenticated administrator connection. Never disable host-key checking.

The gateway's authorized-key entry for this dedicated public key is restricted:

```text
restrict,port-forwarding,permitlisten="127.0.0.1:48181",permitopen="127.0.0.1:48181",command="/usr/bin/false" ssh-ed25519 PUBLIC_KEY arknights-lab-reverse
```

This restricts both listen and direct-forward destinations to the game's gateway
port; shell commands, agent forwarding and PTYs are disabled. Do not replace
existing authorized keys. Do not expose 48181 publicly.

## Caddy

The companion `Caddyfile` is an additional HTTP host route. Validate the complete
candidate configuration with the existing gateway's actual environment before
loading it. Preserve the original configuration and modify the mounted file in
place: this gateway mounts a single file, so replacing its inode can leave the
container seeing the old content.

The current gateway disables its admin API and starts with
`caddy run --config /etc/caddy/Caddyfile --adapter caddyfile`. After validation,
`SIGUSR1` requests a graceful reload of that file; check the resulting route,
existing `/projects`, Caddy logs and a WebSocket after reload. Do not restart
unrelated containers or change the shared zone's SSL mode to deploy this site.

## Domain and TLS

Candidate hostname: `arknights.shzeno.com`. Create an A record pointing to
`43.167.212.176`; do not add an AAAA record without a verified public IPv6 route.
The HTTP preparation route alone is not a finished HTTPS deployment.

For a Caddy-managed public certificate, first create the DNS record as DNS-only,
allow origin TCP 80/443 in the cloud security group, switch the Caddy site address
from `http://arknights.shzeno.com` to `arknights.shzeno.com`, validate/reload and
verify certificate issuance. Then enable Cloudflare proxying and verify the
hostname uses Full (strict) TLS. An alternative is a matching Cloudflare Origin
CA certificate installed at the origin before enabling strict proxying.

No Cloudflare rule needs to expose port 38181 or 48181. Cloud security groups
belong to the cloud provider, not Cloudflare. Keep Cloudflare WebSockets enabled;
do not cache game state, `/ws`, or `/api/collect/*`. Any zone-specific 403 must be
diagnosed for this hostname rather than disabling protection for other sites.

## Verify and stop

On `szh`, `curl http://127.0.0.1:48181/healthz` must report
`collect.automatic=true`. Check the HTTP preparation route with a Host header:

```sh
curl -H 'Host: arknights.shzeno.com' http://127.0.0.1/healthz
```

Before considering the public site ready, verify HTTPS, a real WebSocket,
multiplayer reconnects and captured actions/recordings through the public URL.

To stop this gateway path without stopping the game, remove only this host
route, validate/reload Caddy, then run on the lab:

```sh
systemctl --user disable --now arknights-tunnel.service
```

The dedicated authorized-key entry can then be removed. Keep collected files.
