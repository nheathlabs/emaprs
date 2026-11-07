<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2026 Nicholas Heath -->

# Security policy

## Not meant to be reachable from the internet

emaprs is meant for your station's own network. **Don't make it reachable
from the internet directly**: no port forwarding to it, and no running it on
a public address.

**There is currently no login, by design.** Anyone who can reach emaprs's
port can see everything it shows. Once sending packets is added (see
"Possible future work" in the [README](README.md)), anyone who can reach it
could also transmit through your TNC under your callsign.

**Use emaprs at your own risk, and secure it as you see fit for *your*
network.** Some options:

- `[http] bind` limits which addresses emaprs listens on, e.g.
  `["127.0.0.1", "::1"]` for this machine only (see "Listening addresses"
  in the README);
- a firewall rule limiting who can reach the port;
- a VPN, if you want to reach the station from elsewhere.

## Supported versions

Only the latest release gets fixes.

## What to report

Even on a trusted network, these are bugs worth reporting:

- **Anything received over the air reaching the page as code.** Packets
  come from any station in range, so their text (callsigns, object names,
  comments, messages) must only ever be shown as text.
- A packet, KISS frame or gpsd message that crashes emaprs, hangs it, or
  makes it use unbounded memory or disk.
- emaprs serving files from outside its own `static/` folder, or writing
  anywhere other than its database.

Something that needs the attacker to already reach the web page from the
internet is, by the policy above, not a vulnerability in itself, but say
so if you think emaprs makes it worse than it needs to be.

## Reporting

Open an issue at <https://github.com/nheathlabs/emaprs/issues>, or better
yet a pull request with a fix.

Include the emaprs version (`emaprs --version`, or the Display Options
tab) and how to reproduce the problem. For a problem caused by a packet,
include the raw packet, as shown in the Raw tab.
