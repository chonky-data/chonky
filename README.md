# 🐱 Chonky - a simple Version Control System
[![CI](https://github.com/chonky-data/chonky/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/chonky-data/chonky/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/chonky-data/chonky/blob/main/LICENSE)
[![GitHub](https://img.shields.io/badge/repo-github-green.svg)](https://github.com/chonky-data/chonky)
## About
"Chonky files need Chonky version control"

Chonky syncs large binary files (game assets, ML models, test data) between a
local workspace and a remote object store. File revisions are recorded in a
text file called `CHONKY`. Chonky itself doesn't version this file - you manage
it however you like: commit it to Git, another VCS, or track it manually.

### Why Chonky?

Tools like [Git LFS](https://git-lfs.com/) solve a similar problem but are
tightly coupled to Git. LFS downloads all tracked files by default, and while
include/exclude patterns exist, they're repository-wide settings rather than
independent definitions you can commit per-project.

Chonky is decoupled from your VCS entirely. Each `CHONKY` file defines an
independent set of assets, and you choose which to sync:

```bash
chonky --config=tests/CHONKY sync   # Pull only test data
chonky --config=assets/CHONKY sync  # Pull only game assets
chonky --config=models/CHONKY sync  # Pull only ML model weights
chonky sync                         # Recursively pull all repos
```

This makes it easy to integrate with build systems that fetch resources
on-demand rather than upfront.

### Features

- **Serverless** - Backed by standard object stores (S3, MinIO) rather than a
  dedicated server. [Easy to add new backends.](chonky/s3_remote.py)
- **[Monorepo](https://en.wikipedia.org/wiki/Monorepo) friendly** - Multiple
  Chonky repositories can coexist within a single parent repository. Selectively
  sync only what you need (e.g. pull test data only when running tests).
- **Shared storage** - Multiple Chonky repositories can point to the same object
  store. Adding a new repository is as simple as copying a `CHONKY` file, and
  migrating files between repositories is zero-copy.
- **No system dependencies** - Pure Python. Install via pip, Git submodule, or
  vendor directly into your project.

## Setup
### AWS S3
- [Setup an S3 Bucket](https://docs.aws.amazon.com/AmazonS3/latest/userguide/GetStartedWithS3.html).
  - Recommended: don't enable versioning since it uses SHA1 as object keys and thus should never overwrite an object.
  - Recommended: only read/write access is required for users that you want to have access.
- Login / Install AWS Credentials -> `aws configure`
### CHONKY config
The `CHONKY` file configures the remote object store and workspace. It also
tracks the HEAD version of each file in the Chonky Repository.

To create a new repository, create a file named `CHONKY` in your parent VCS:
```
[config]
type = s3
bucket = MyChonkyBucket
endpoint = s3.us-east-1.amazonaws.com
workspace = Assets/

[HEAD]
```

For S3-compatible services (e.g. MinIO), use your service's endpoint:
```
[config]
type = s3
bucket = my-bucket
endpoint = http://minio-server:9000
workspace = Assets/

[HEAD]
```
### Exclude Workspace from parent VCS
While the `CHONKY` file should be tracked by your parent VCS, the Chonky
Workspace should be excluded. In Git this can be done by adding the Chonky
Workspace to the `.gitignore` file, for example:
```
Assets/
```
### Install Chonky
Example of installing Chonky inside a Python Virtual Environment:
```
python -m venv .venv
source .venv/bin/activate
pip install "git+ssh://git@github.com/chonky-data/chonky.git@main"
```

## Usage
```
# Display differences between the remote and workspace
$ chonky status
  Workspace is up to date with the remote.
  Workspace has changes:
    missing   cats/milo.jpg

# Pull changes from remote into the local workspace
$ chonky sync

# Push changes from local workspace to the remote
$ chonky submit

# Revert local workspace changes
$ chonky revert
```
