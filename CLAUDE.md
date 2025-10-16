# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Docker Atelier is a professional PostgreSQL development environment providing:
- **16 environment combinations**: 4 OS distributions × 4 PostgreSQL versions (14, 15, 16, 17)
- **48 container instances**: Each environment runs in Active-Standby-Standalone configuration
- Full PostgreSQL source development with integrated tools (JED editor, tmux, Valgrind, Git)
- Complete CloudBerry database development support

## Repository Structure

```
docker-atelier/
├── docker-build             # Main build script with retry logic
├── docker-*.yml             # Docker Compose configs (pg14-17, full, develop)
├── tmux-session.py          # Python-based tmux session manager
├── tmux-*.yml               # tmux session configurations
├── .env                     # Network and port configuration
├── dockerfiles/             # All Dockerfile definitions
│   ├── rocky/{8,9}/         # Rocky Linux Dockerfiles
│   │   ├── Dockerfile-init  # Base OS image
│   │   └── Dockerfile-pgxx  # PostgreSQL image (parameterized)
│   ├── ubuntu/{22,24}/      # Ubuntu Dockerfiles
│   └── files/               # Shared configuration files
│       ├── pgsql_bashrc     # PostgreSQL development functions
│       ├── gitconfig        # Git configuration
│       └── *.json           # VSCode configs
└── volumes/                 # Service configurations
    ├── haproxy/
    ├── apache2/
    └── squid/
```

## Build System

### Building Images

```bash
# Build all images (uses Squid proxy for acceleration)
./docker-build

# Images are built in order:
# 1. Init images: rocky8-init, rocky9-init, ubuntu22-init, ubuntu24-init
# 2. PostgreSQL images: {os}-pg{14,15,16,17} for each OS
```

**Build process:**
- Uses `Dockerfile-pgxx` with `PG_MAJOR` build arg for all PostgreSQL versions
- Implements automatic retry mechanism (max 10 attempts with 10s delay)
- Leverages Squid proxy at `${ATELIER_SQUID_PORT}` for package caching
- Requires `.git-credentials` file for private repositories

### Docker Compose

```bash
# Start default environment (mixed versions)
docker compose up -d

# Start specific PostgreSQL version
docker compose -f docker-pg14.yml up -d
docker compose -f docker-pg15.yml up -d
docker compose -f docker-pg16.yml up -d
docker compose -f docker-pg17.yml up -d

# Start all versions
docker compose -f docker-full.yml up -d
```

## Network Architecture

**IP Allocation Pattern:**
- Rocky8: `${ATELIER_SUBNET}.8.x`
- Rocky9: `${ATELIER_SUBNET}.9.x`
- Ubuntu22: `${ATELIER_SUBNET}.22.x`
- Ubuntu24: `${ATELIER_SUBNET}.24.x`

**Service IPs:**
- Init: `.x.1`
- PG14 Active/Standby/Standalone: `.x.14/.x.44/.x.74`
- PG15 Active/Standby/Standalone: `.x.15/.x.45/.x.75`
- PG16 Active/Standby/Standalone: `.x.16/.x.46/.x.76`
- PG17 Active/Standby/Standalone: `.x.17/.x.47/.x.77`
- HAProxy: `${ATELIER_SUBNET}.200.1`
- Apache2: `${ATELIER_SUBNET}.200.2`
- Squid: `${ATELIER_SUBNET}.200.3`

**Environment Variables (.env):**
```bash
ATELIER_SUBNET=172.30        # Network base
ATELIER_SSH_PORT=8022        # HAProxy SSH port
ATELIER_HTTP_PORT=8080       # Apache HTTP port
ATELIER_SQUID_PORT=3128      # Squid proxy port
```

## tmux Session Management

### Python-based Session Manager

The `tmux-session.py` script provides intelligent tmux session management:

**Key Features:**
- Reads YAML configuration (windows/panes structure)
- Filters services based on running containers
- Incremental updates (creates/deletes only what changed)
- Preserves existing panes and their state
- Detects dead panes and recreates them

**Usage:**
```bash
# Using wrapper scripts
./tmux-docker          # Default configuration
./tmux-atelier         # Atelier-specific config

# Direct invocation
python3 tmux-session.py tmux-docker.yml [session_name]

# Debug mode
TMUX_DEBUG=1 python3 tmux-session.py tmux-docker.yml

# Detached mode (print attach command only)
TMUX_DETACH=1 python3 tmux-session.py tmux-docker.yml
```

**Configuration Format (tmux-*.yml):**
```yaml
session: atelier
windows:
  - name: monitor
    layout: tiled  # or even-horizontal, even-vertical, main-vertical
    panes:
      - title: btop
        command: bash -c 'while sleep 1; do btop; done'
        service: haproxy  # optional: filters by docker compose service
      - title: shell
        command: bash
    resize_panes:  # optional custom sizing
      - target: 0
        x: 200
        y: 55
```

**tmux Keybindings (custom):**
- `Ctrl+B, Ctrl+S`: Toggle pane synchronization
- Mouse click on status right: Toggle pane sync
- Mouse click on status left: Switch to next session

## PostgreSQL Development Functions

All functions are defined in `dockerfiles/files/pgsql_bashrc` and available in containers:

### Build & Configure

```bash
# Configure PostgreSQL from installed pg_config
pg-configure [release|debug|valgrind|coverage]
# - release: Standard build
# - debug: -Og -ggdb, no FORTIFY_SOURCE
# - valgrind: debug + -DUSE_VALGRIND
# - coverage: debug + --enable-coverage

# Build PostgreSQL
pg-make [make_options]  # Runs: make world "$@"

# Install PostgreSQL + extensions
pg-install  # Installs: postgres, pg_ensure_queryid, pg_store_plans, pgsentinel

# Clean build
pg-clean
```

### CloudBerry Development

```bash
cb-configure  # Configure CloudBerry with optimized CFLAGS
cb-make       # Build CloudBerry (main, contrib, gpcontrib, gpMgmt)
cb-clean      # Clean build artifacts
```

### Service Management

```bash
pg-start      # systemctl start postgresql[-{version}]
pg-restart    # systemctl restart postgresql[-{version}]
pg-stop       # systemctl stop postgresql[-{version}]
pg-status     # systemctl status postgresql[-{version}]
pg-kill       # Direct kill via postmaster.pid
```

### Git Workspace Functions

```bash
git-pull      # Fetch all remotes, create main/master branches, pull
git-update    # Update git submodules recursively
git-clean     # Clean all workspace repos (git clean -xdf)
git-log       # Paginated git log output
```

### Workspace Synchronization

```bash
rsync-workspace [source_role]
# Default behavior based on current role:
# - From active -> sync from standalone
# - From standby -> sync from standalone
# - From standalone -> sync from active
```

### Debugging

```bash
pg-valgrind           # Run PostgreSQL under Valgrind
pg-trim-valgrind      # Remove empty Valgrind log files
pg-core <core-file>   # Analyze core dump with GDB
pg-lcov clear         # Clear coverage counters
pg-lcov report        # Generate HTML coverage report
```

### Database User Management

```bash
pg-user [experdba|postgres]
# experdba: Sets PGHOST=localhost, PGUSER=experdba, etc.
# postgres: Unsets connection vars (uses defaults)
# No args: Shows current configuration
```

**Prompt Format:**
- `p@r08-p14-a:~$` = postgres@rocky8-pg14-active
- `p@u22-pg15-s:~$` = postgres@ubuntu22-pg15-standby
- `p@r09-pg16-#:~$` = postgres@rocky9-pg16-standalone

## Container Access

```bash
# Direct exec
docker compose exec rocky8-pg15-active bash

# Via tmux (recommended)
./tmux-docker-pg15
tmux attach-session -t atelier
```

**Default Credentials:**
- PostgreSQL superuser: `postgres` (no password locally)
- Development user: `experdba` / `experdba`
- Development database: `experdb`

## Workspace Layout

Each PostgreSQL container has:
```
/var/lib/pgsql/workspace/
├── .gitconfig              # Global git config
├── .vscode/               # VSCode settings
├── postgres/              # PostgreSQL source (origin: assam258-5892, upstream: postgres)
├── pg_store_plans/        # Extension (origin: experdb, upstream: ossc-db)
├── pgsentinel/            # Extension (origin: experdb, upstream: pgsentinel)
├── pg_ensure_queryid/     # Extension (origin: experdb)
├── cloudberry/            # CloudBerry source (origin: apache)
└── gpbackup/              # gpbackup tool (origin: cloudberrydb)
```

**Important:** Workspaces are persistent via Docker volumes:
- `{container-name}-workspace:/var/lib/pgsql/workspace`
- `{container-name}-vscode-server:/var/lib/pgsql/.vscode-server`

## Adding New Components

### New PostgreSQL Version

1. Update `docker-build` script: Add version to `pg_vers` array
2. Dockerfiles use `PG_MAJOR` build arg - no file changes needed
3. Create new compose file: `cp docker-pg17.yml docker-pg18.yml`
4. Update service names and IP addresses in compose file

### New OS Distribution

1. Create directory: `dockerfiles/{distro}/{version}/`
2. Copy and adapt: `Dockerfile-init` and `Dockerfile-pgxx`
3. Update package manager commands (dnf/apt)
4. Update `docker-build` script: Add to `distros` array
5. Create corresponding compose file

### New Extension Module

Add to `Dockerfile-pgxx`:
```dockerfile
RUN git clone --depth 1 --branch {version} {repo} && \
    cd {extension} && \
    PATH=/usr/pgsql-${PG_MAJOR}/bin:${PATH} make install && \
    cd .. && rm -rf {extension}
```

Add to `pg-install` function in `pgsql_bashrc`.

## Important Configuration Files

- **pgsql_bashrc**: All PostgreSQL development functions and aliases
- **gitconfig**: Git user config, URL rewrites (GitLab → GitHub mapping)
- **postgresql.auto.conf**: Default PostgreSQL settings for all containers
- **role.sql**: Creates experdba user and experdb database
- **extension.sql**: Enables default extensions (pg_stat_statements, etc.)

## Common Development Workflows

### Full Build & Install Cycle

```bash
# Inside container
cd ~/workspace/postgres
git-pull                          # Update all repos
pg-configure debug                # Configure for debugging
pg-make -j$(nproc)               # Parallel build
pg-install                        # Install PG + extensions
pg-restart                        # Restart service
```

### Testing Across Versions

```bash
# Start all versions
docker compose -f docker-full.yml up -d

# Use tmux to access all simultaneously
./tmux-docker-full
tmux attach -t atelier

# Enable pane sync: Ctrl+B, Ctrl+S
# Now commands execute in all panes
```

### Workspace Synchronization

```bash
# Work in active, then distribute to others
docker compose exec rocky8-pg15-active bash
# ... make changes ...
docker compose exec rocky8-pg15-standby bash
rsync-workspace active  # Pull from active to standby
```

## Key Technical Details

- **Privileged containers**: Required for systemd (/usr/sbin/init entrypoint)
- **Extra hosts**: Each container has DNS aliases (init, active, standby, standalone)
- **Build secrets**: Uses BuildKit secrets for .git-credentials
- **File limits**: ulimit -n 1048576 (for large file operations)
- **Core dumps**: Saved to /tmp/core-{name}-{time}-{pid}
- **Proxy support**: All builds route through Squid for caching
