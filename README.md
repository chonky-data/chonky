# 🐱 Chonky - a simple Version Control System
[![CI](https://github.com/chonky-data/chonky/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/chonky-data/chonky/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/chonky-data/chonky/blob/main/LICENSE)
[![GitHub](https://img.shields.io/badge/repo-github-green.svg)](https://github.com/chonky-data/chonky)
## About
"Chonky files need Chonky version control"

Chonky is designed to supplement existing version control systems that are
typically unable to natively handle large files well (e.g. Git). For instance,
large game assets, test data, and ML models.

Chonky tries to be [monorepo](https://en.wikipedia.org/wiki/Monorepo) friendly,
it does this by not forcing a 1-to-1 relationship with the parent VCS, which
means you can have several Chonky sub-repositories within a parent repository.
This makes it easy for developers to setup custom workflows in their build
system to only pull the data they need (e.g. sync test-data only when running
tests).

Chonky is itself an extremely simple Version Control System (VCS) designed to be
embedded within another VCS such as Git for the purposes of managing large
binary assets. Chonky does not though take opinions on what that parent VCS is,
infact its entirely optional. It accomplishes this by allowing the parent VCS to
manage a single text file per Chonky repository which represents the HEAD
revision of each tile.

Chonky has minimal infrastructure management. It does not require a dedicated
server process! Instead it can be backed by a generic object-store or 
filesystem. Currently AWS S3 is supported.

Chonky is written entirely in Python and it's install is designed to be manged
either via including it directly into the parent repository, Git Submodule, or
Python Venv. This avoids each client needing to manage their own install of yet
another system-wide dependency. This also ensures users are always using the
right version of Chonky for the current project.

## Setup
### AWS S3
- [Setup an S3 Bucket](https://docs.aws.amazon.com/AmazonS3/latest/userguide/GetStartedWithS3.html).
  - Recommended: don't enable versioning since uses SHA1 as object keys and thus never overwrites an object.
  - Recommended: only read/write access is required for users that you want to have access.
- [Install AWS Credentials on local clients](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html).
  - Typical location: `~/.aws/credentials`
### CHONKY config
The `CHONKY` file points at both the remote object store ("remote"), and the
Chonky Workspace root ("workspace"), as well as will track the HEAD version of
any files in the Chonky Repository.

To create a new repository, create a file named `CHONKY` in your parent VCS:
```
[config]
remote = s3://MyChonkyBucket
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
pip install "git+ssh://git@github.com/jamesdolan/chonky.git@main"
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
