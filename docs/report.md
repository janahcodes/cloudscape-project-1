# Project 1: First Cloud Deployment

**Student:** Jana Saleh  
**Course:** AI 255 Cloud Computing Concepts
**Provider:** AWS (us-east-2)  
**Live endpoint:** https://app.cloudscape.lol

## 1. What I built

CloudDrop moves files between your own devices, for example from your
phone to your laptop, without accounts or cables:

1. On one device, click **Start a room**. You get a 6-character room code and a QR code.
2. On the other device, scan the QR code or type the code.
3. Upload on either device. The file appears on both within about 3 seconds,
   and photos show a preview.
4. Files are deleted automatically after 24 hours.



| Requirement | How it is met |
|---|---|
| Compute | One EC2 t3.micro instance (Amazon Linux 2023) runs the FastAPI app |
| Object storage | Files are stored in a private S3 bucket under `rooms/<CODE>/`. An S3 lifecycle rule deletes them after 1 day |
| DNS | Route 53 hosted zone for `cloudscape.lol` (domain bought on Porkbun) with an A record for `app.cloudscape.lol` |
| TLS | Caddy on the instance gets a free Let's Encrypt certificate, serves HTTPS, and redirects HTTP to HTTPS |
| Scoped IAM role | The instance role can only list, read, write and delete objects under `rooms/` in that one bucket |

All AWS resources are created by Terraform in `infra/`, so the whole deployment can
be rebuilt or destroyed with one command.

**How a request flows:**
1. The browser looks up `app.cloudscape.lol` in Route 53 and connects to the instance on port 443.
2. Caddy handles TLS and forwards the request to the app on `127.0.0.1:8000`.
3. The app reads and writes S3 using the instance role's temporary credentials.
   An open room page asks the app for the room's file list every 3 seconds.
4. For downloads and image previews, the app does not send the file itself. It
   gives the browser a presigned S3 URL that is valid for 5 minutes, so the
   bucket stays private but the browser can fetch the file directly from S3.

## 2. Design decisions

| Decision | Why | Alternative I considered |
|---|---|---|
| **EC2 virtual machine** (IaaS) | The brief asks for compute; a VM shows most clearly what I manage and what AWS manages | Lambda or App Runner. Simpler, but they hide the server |
| **t3.micro** | Enough for a demo; cheapest general-purpose size | t3.small, only needed at higher traffic (described on cost estimate page) |
| **Caddy + Let's Encrypt for TLS** | Free certificate, automatic renewal, works on a single instance | An Application Load Balancer with an AWS ACM certificate. Better at scale but adds about $16+/month |
| **Default VPC**, security group allowing only ports 80 and 443 | Nothing else is needed for one public server; no SSH port is open | A custom VPC with private subnets. More layers of protection, but more parts than this project needs |
| **Room codes instead of accounts** | No sign-up, which suits a quick phone-to-laptop transfer. Each room only sees its own files | User accounts with passwords. More secure, but a lot more to build |
| **Private S3 bucket + presigned URLs** | Files are never public; links expire after 5 minutes | Making the bucket public. Simpler, but anyone could list or read every file |
| **S3 lifecycle rule (delete after 1 day)** | S3 does the cleanup itself, so no server or scheduled job is needed, and storage cost stays tiny | A cleanup script on the server, which is one more thing that can fail |
| **Polling every 3 seconds** | Simple and works through any network | WebSockets push. Faster and cheaper at scale, but more complex (see cost estimate) |
| **IAM role, not access keys, on the server** | No long-lived secret is stored on the instance; AWS rotates the role's credentials automatically | Putting access keys in a config file. Easy to leak, and they never expire |
| **Terraform** | Repeatable and reviewable; one command builds or destroys everything | Clicking in the console. Not repeatable, and hard to show what was configured |

### Least privilege
The role's policy allows:
- `s3:ListBucket` on the bucket, but only when listing under the `rooms/` prefix;
- `s3:GetObject`, `s3:PutObject` and `s3:DeleteObject` only on `rooms/*` in that bucket.

It cannot read other buckets or other folders in this bucket, change IAM, or
start instances. If the server were compromised, the damage would be limited
to temporary room files that are deleted within a day anyway.

### Encryption
- In transit: browser ↔ server over HTTPS (Let's Encrypt). Server ↔ S3 and browser ↔ S3 (presigned URL) over HTTPS.
- At rest: S3 encrypts every object by default (SSE-S3).

### Shared responsibility
AWS is responsible for the physical data centres, the hypervisor, and the S3
and Route 53 services. I am responsible for the OS on the instance, the app
code, the security group rules, the IAM permissions, and keeping the bucket private.

## 3. Testing

Automated tests: tests/test_app.py`, 20 tests, all passing). They run against
an in-memory fake of S3 (moto), so they need no AWS account:

```
.venv/bin/pytest -q      →  20 passed
```

They cover:
- creating rooms, and that room codes are valid and random;
- the room page, its QR code, and redirecting lowercase codes;
- upload, list, download and delete;
- that rooms can't see each other's files, and that images get a preview link;
- 404s for missing rooms and files;
- rejecting empty uploads, oversized uploads and path-traversal ids;
- that files are stored under `rooms/` (both the IAM policy and the lifecycle
  rule depend on this);
- that download links use the regional S3 address. This is a regression test
  for the bug in section 5.

**Local check in a browser:** before deploying I ran the app on my laptop
against fake S3 and checked the home page, a room, the auto-refresh, and the
phone-width layout.

**Checks on the live deployment** (with `curl` and `openssl`, 25 Oct 2026):

| Check | Result |
|---|---|
| `/ready` (app can reach S3 with its scoped role) | `{"status":"ready"}` |
| Create room → room page → QR code | 200; the QR code encodes `https://app.cloudscape.lol/r/<CODE>` |
| Upload a text file and a PNG → list | Both listed; the PNG has a preview link |
| Image preview link | 200 `image/png`, served directly by S3 |
| Download | Same text as uploaded, with `Content-Disposition: attachment` |
| Delete | 204 |
| Different room | Empty list (cannot see the first room's files) |
| `http://` request | 308 redirect to `https://` |
| Certificate | Issuer Let's Encrypt, valid until 25 Jan 2027 |
| Opening the S3 bucket URL directly | 403 Forbidden (bucket is private) |
| S3 lifecycle rule | `expire-room-files`, prefix `rooms/`, 1 day, Enabled |

## 4. Cost (summary)

Full numbers are in cost-estimate.md

| Traffic | Monthly cost |
|---|---|
| Low: 10,000 requests | ≈ $12.43 |
| Medium: 1 million requests | ≈ $24.70 |
| High: 50 million requests | ≈ $525, or ≈ $288 with two optimisations |

At low traffic I mostly pay for a server that is idle. At high traffic the
biggest costs are data transfer out and the S3 `LIST` calls caused by polling
every 3 seconds. A choice that costs nothing in a demo becomes about $200/month
at scale.

## 5. What did not work, and how I fixed it

1. **Could not register the domain in Route 53.** AWS Free-plan accounts can't
   buy domains. I bought `cloudscape.lol` on Porkbun, created a Route 53 hosted
   zone, and changed Porkbun's nameservers to the four AWS nameservers.
2. **The us-east-1 region was blocked.** `terraform plan` failed with
   `explicit deny in a service control policy`. My account belongs to an AWS
   Organization whose policy only allows EC2 in us-east-2. I moved the project
   to us-east-2; prices there are the same.
3. **The first server had no internet access.** Terraform failed to create the
   outbound security-group rule because its description contained an apostrophe,
   which AWS doesn't allow there. The instance started without outbound access,
   so its setup script couldn't install anything. I removed the apostrophe and
   had Terraform replace the instance.
4. **Downloads failed with `SignatureDoesNotMatch`.** boto3 generated presigned
   URLs for the global S3 address (`s3.amazonaws.com`). Because the bucket is in
   us-east-2, S3 redirected the browser to the regional address, and the
   redirect broke the signature. I fixed it by giving boto3 the regional
   endpoint (`https://s3.us-east-2.amazonaws.com`) and added a test for it.
   This would not have happened in us-east-1, so the region change exposed it.
5. **The first version was not useful.** It was one shared public file list
   where anyone could delete anything. I redesigned it around private rooms,
   which also let me tighten the IAM role to `rooms/*` and add the lifecycle rule.
6. **Setting up CLI access.** The IAM console no longer showed the console
   password option I expected, so I used an access key for my IAM user with
   `aws configure`. I first pasted it into AWS CloudShell by mistake instead of
   my own terminal, so I deleted that key and created a new one.

## 6. Limitations
- **One server, one Availability Zone.** If the instance or its zone fails, the
  site is down. Fixing that needs a load balancer and an auto-scaling group
  across two zones.
- **Code changes replace the server.** The app is shipped inside the instance's
  startup script, so every code change creates a new instance. That causes a
  few minutes of downtime and a new certificate (Let's Encrypt allows 5 per week
  for the same name).
- **Polling** every 3 seconds is simple but wasteful at scale (see cost estimate).
- **No monitoring or alerts** beyond the server's own logs.

## 7. Use of AI tools
I used Claude to helped me with:
- write the first versions of the app code, and Terraform files
- debug the problems in section 5 (region block, security-group error, presigned URL signature);
- redesign the service around rooms;
- draw the architecture diagram and work out the cost estimate

I created the AWS account and IAM user, bought and connected the domain, ran
the deployment, and checked the results myself and went through a final review of every file.